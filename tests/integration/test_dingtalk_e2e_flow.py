from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime
from chatdba.domain.models import TaskStatus
from chatdba.domain.report_schema import OptimizationReport
from chatdba.tasks.service import OptimizationTaskService


class RecordingSender:
    def __init__(self):
        self.messages = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.messages.append(
            {
                "conversation_id": conversation_id,
                "session_webhook": session_webhook,
                "text": text,
            }
        )


def test_dingtalk_runtime_runs_sql_optimization_and_streams_report():
    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        assert task_payload["raw_sql"] == "select * from orders"
        if progress_sink:
            progress_sink("Parsing SQL\n")
            progress_sink("Built optimization report\n")
        return {
            "report": OptimizationReport.model_validate(
                {
                    "task_id": "task-1",
                    "summary": "Use an index to avoid filesort.",
                    "confidence": 0.35,
                    "confidence_label": "low",
                    "evidence_status": "sql_only",
                    "missing_evidence": ["route_info", "explain_json", "create_table"],
                    "limitations": ["未获取到源库执行证据，报告基于 SQL 文本、规则和历史案例生成。"],
                    "bottlenecks": [{"code": "limit_with_order_by", "evidence": "ORDER BY with LIMIT may require a supporting index."}],
                    "sql_rewrites": [],
                    "index_recommendations": [],
                    "risks": [],
                    "validation_steps": ["先在目标库测试环境执行 EXPLAIN 与回归测试，再决定是否上线。"],
                    "similar_cases": [],
                }
            )
        }

    sender = RecordingSender()
    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_id_factory=lambda: "task-1",
    )
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=DingTalkResponder(sender),
        stream_interval_ms=1000,
    )
    runtime = DingTalkStreamRuntime(handler=handler.handle)

    result = runtime.handle_test_message(
        DingTalkInboundMessage(
            message_id="msg-1",
            conversation_id="conv-1",
            sender_id="user-1",
            text="SQL优化 select * from orders",
            session_webhook="https://example.test/webhook",
        )
    )

    assert result.task_id == "task-1"
    assert result.status == TaskStatus.COMPLETED
    assert [message["text"] for message in sender.messages][0] == SQL_OPTIMIZATION_STARTED_MESSAGE
    full_stream_text = "".join(message["text"] for message in sender.messages[1:])
    assert "# SQL优化报告" in full_stream_text
    assert "## SQL重写建议" in full_stream_text
    assert "## 索引推荐" in full_stream_text
