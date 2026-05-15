import io
import json
import urllib.error

import pytest

from chatdba.alarm_binlog.models import AlarmWebhookSettings
from chatdba.alarm_binlog.webhook_sender import AlarmWebhookSender


class RecordingResponse:
    def close(self) -> None:
        pass


def test_webhook_sender_posts_markdown_payload_to_fixed_group_webhook():
    seen = {}

    def fake_opener(request):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = request.data
        return RecordingResponse()

    sender = AlarmWebhookSender(
        AlarmWebhookSettings(
            url="https://oapi.dingtalk.com/robot/send?access_token=token",
            timeout_seconds=8,
        ),
        opener=fake_opener,
    )

    sender.send_markdown(title="智能诊断报告", markdown="### 一、问题简述\nCPU 高")

    assert seen["url"].startswith("https://oapi.dingtalk.com/robot/send")
    assert seen["method"] == "POST"
    assert seen["headers"]["Content-type"] == "application/json"
    assert json.loads(seen["body"].decode("utf-8")) == {
        "msgtype": "markdown",
        "markdown": {
            "title": "智能诊断报告",
            "text": "### 一、问题简述\nCPU 高",
        },
    }


def test_webhook_sender_surfaces_http_errors():
    def failing_opener(request):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"errcode":123,"errmsg":"invalid"}'),
        )

    sender = AlarmWebhookSender(
        AlarmWebhookSettings(url="https://example.test/webhook"),
        opener=failing_opener,
    )

    with pytest.raises(RuntimeError, match="HTTP 400"):
        sender.send_markdown(title="智能诊断报告", markdown="content")
