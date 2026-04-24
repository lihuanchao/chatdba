from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX,
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_USAGE_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkSendResult
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus, TaskStatus
from chatdba.domain.report_schema import OptimizationReport
from chatdba.tasks.service import OptimizationTaskExecution


class RecordingResponder:
    def __init__(self):
        self.messages = []

    def reply_text(self, message, text):
        self.messages.append(text)
        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )


class SuccessfulTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(
            {
                "raw_sql": raw_sql,
                "dingtalk_context": dingtalk_context,
                "progress_sink": progress_sink,
            }
        )
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return OptimizationTaskExecution(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            result={
                "report": OptimizationReport.model_validate(
                    {
                        "task_id": "task-1",
                        "summary": "Use an index to avoid filesort.",
                        "confidence": 0.35,
                        "confidence_label": "low",
                        "evidence_status": "sql_only",
                        "missing_evidence": [
                            "route_info",
                            "explain_json",
                            "create_table",
                        ],
                        "limitations": [
                            "No source execution evidence was available."
                        ],
                        "bottlenecks": [
                            {
                                "code": "limit_with_order_by",
                                "evidence": "ORDER BY with LIMIT may require a supporting index.",
                            }
                        ],
                        "sql_rewrites": [],
                        "index_recommendations": [],
                        "risks": [],
                        "validation_steps": [
                            "Validate the SQL against the target source database before applying any recommendation."
                        ],
                        "similar_cases": [],
                    }
                )
            },
        )


class FailedTaskService:
    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        return OptimizationTaskExecution(
            task_id="task-2",
            status=TaskStatus.FAILED,
            error="collector unavailable",
        )


def make_message(text: str) -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text=text,
        session_webhook="https://example.test/webhook",
    )


def test_handler_sends_usage_guidance_for_empty_sql():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化"))

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert responder.messages == [SQL_OPTIMIZATION_USAGE_MESSAGE]
    assert service.calls == []


def test_handler_runs_task_and_sends_start_progress_and_report():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-1"
    assert result.status == TaskStatus.COMPLETED
    assert service.calls[0]["raw_sql"] == "select * from orders"
    assert service.calls[0]["dingtalk_context"].conversation_id == "conv-1"
    assert responder.messages[0] == SQL_OPTIMIZATION_STARTED_MESSAGE
    assert responder.messages[1] == "Parsing SQL\n"
    assert "Evidence: SQL_ONLY" in responder.messages[-1]
    assert "Use an index to avoid filesort." in responder.messages[-1]


def test_handler_sends_failure_message_when_task_fails():
    responder = RecordingResponder()
    handler = DingTalkSqlOptimizationHandler(
        task_service=FailedTaskService(),
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-2"
    assert result.status == TaskStatus.FAILED
    assert result.error == "collector unavailable"
    assert responder.messages[-1] == (
        f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}collector unavailable"
    )
