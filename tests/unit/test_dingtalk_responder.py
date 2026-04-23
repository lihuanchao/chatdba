from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.responder import DingTalkResponder


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "session_webhook": session_webhook,
                "text": text,
            }
        )


class FailingSender:
    def send_text(self, *, conversation_id, session_webhook, text):
        raise RuntimeError("network down")


def make_message() -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
    )


def test_responder_sends_text_to_session_webhook():
    sender = RecordingSender()
    responder = DingTalkResponder(sender)

    result = responder.reply_text(make_message(), "hello")

    assert result.ok is True
    assert result.conversation_id == "conv-1"
    assert result.message == "hello"
    assert result.error is None
    assert sender.calls == [
        {
            "conversation_id": "conv-1",
            "session_webhook": "https://example.test/webhook",
            "text": "hello",
        }
    ]


def test_responder_captures_sender_errors():
    responder = DingTalkResponder(FailingSender())

    result = responder.reply_text(make_message(), "hello")

    assert result.ok is False
    assert result.conversation_id == "conv-1"
    assert result.message == "hello"
    assert result.error == "network down"
