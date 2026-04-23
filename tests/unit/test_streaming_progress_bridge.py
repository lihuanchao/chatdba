from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.responder import DingTalkSendResult


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


def make_message() -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
    )


def test_progress_bridge_flushes_when_interval_elapsed():
    clock_values = iter([0, 500, 1500])
    responder = RecordingResponder()
    bridge = StreamingProgressBridge(
        responder=responder,
        message=make_message(),
        interval_ms=1000,
        clock_ms=lambda: next(clock_values),
    )

    bridge.emit("Parsing SQL\n")
    bridge.emit("Collecting EXPLAIN\n")

    assert responder.messages == ["Parsing SQL\nCollecting EXPLAIN\n"]
    assert [result.message for result in bridge.send_results] == [
        "Parsing SQL\nCollecting EXPLAIN\n"
    ]


def test_progress_bridge_finish_force_flushes_remaining_chunks():
    responder = RecordingResponder()
    bridge = StreamingProgressBridge(
        responder=responder,
        message=make_message(),
        interval_ms=1000,
        clock_ms=lambda: 0,
    )

    bridge.emit("Generated diagnostic findings\n")
    bridge.finish()

    assert responder.messages == ["Generated diagnostic findings\n"]
