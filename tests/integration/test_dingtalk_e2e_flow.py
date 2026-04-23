from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime
from chatdba.domain.models import TaskStatus
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


def test_dingtalk_runtime_runs_sql_optimization_and_streams_replies():
    def fake_runner(task_payload, collector, progress_sink=None):
        assert task_payload["raw_sql"] == "select * from orders"
        if progress_sink:
            progress_sink("Parsing SQL\n")
            progress_sink("Generated diagnostic findings\n")
        return {"findings": []}

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
    assert [message["text"] for message in sender.messages] == [
        SQL_OPTIMIZATION_STARTED_MESSAGE,
        "Parsing SQL\nGenerated diagnostic findings\n",
        SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    ]
