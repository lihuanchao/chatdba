import io
import json
import urllib.error

import pytest

from chatdba.dingtalk.sender import DingTalkSessionWebhookSender


class RecordingResponse:
    def __init__(self, body: bytes = b"{}"):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_sender_posts_text_payload_to_session_webhook():
    seen = {}

    def fake_opener(request):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = request.data
        return RecordingResponse()

    sender = DingTalkSessionWebhookSender(opener=fake_opener)

    sender.send_text(
        conversation_id="conv-1",
        session_webhook="https://example.test/webhook",
        text="hello dingtalk",
    )

    assert seen["url"] == "https://example.test/webhook"
    assert seen["method"] == "POST"
    assert seen["headers"]["Content-type"] == "application/json"
    assert json.loads(seen["body"].decode("utf-8")) == {
        "msgtype": "text",
        "text": {"content": "hello dingtalk"},
    }


def test_sender_requires_session_webhook():
    sender = DingTalkSessionWebhookSender(opener=lambda request: RecordingResponse())

    with pytest.raises(RuntimeError, match="Missing session webhook"):
        sender.send_text(
            conversation_id="conv-1",
            session_webhook=None,
            text="hello dingtalk",
        )


def test_sender_surfaces_http_errors():
    def failing_opener(request):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"errcode":123,"errmsg":"invalid"}'),
        )

    sender = DingTalkSessionWebhookSender(opener=failing_opener)

    with pytest.raises(RuntimeError, match="HTTP 400"):
        sender.send_text(
            conversation_id="conv-1",
            session_webhook="https://example.test/webhook",
            text="hello dingtalk",
        )
