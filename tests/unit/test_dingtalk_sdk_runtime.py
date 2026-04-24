from types import SimpleNamespace

import pytest

from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkImportError,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.domain.models import TaskStatus


class RecordingHandler:
    def __init__(self):
        self.message = None

    def handle(self, message):
        self.message = message
        return SimpleNamespace(
            accepted=True,
            status=TaskStatus.COMPLETED,
            task_id="task-1",
        )


def test_sdk_handler_maps_callback_data_to_inbound_message():
    app_handler = RecordingHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)

    result = adapter.handle_callback_data(
        {
            "msgId": "msg-1",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "text",
            "text": {"content": "SQL优化 select * from orders"},
        }
    )

    assert result.task_id == "task-1"
    assert app_handler.message.message_id == "msg-1"
    assert app_handler.message.conversation_id == "conv-1"
    assert app_handler.message.sender_id == "user-1"
    assert app_handler.message.session_webhook == "https://example.test/webhook"
    assert app_handler.message.text == "SQL优化 select * from orders"


def test_sdk_handler_uses_empty_text_for_non_text_payload():
    app_handler = RecordingHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)

    adapter.handle_callback_data(
        {
            "msgId": "msg-2",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "picture",
            "content": {"downloadCode": "abc"},
        }
    )

    assert app_handler.message.text == ""


def test_load_dingtalk_stream_sdk_raises_clear_error():
    def fake_import_module(name: str):
        raise ImportError(name)

    with pytest.raises(
        DingTalkSdkImportError, match="dingtalk-stream is not installed"
    ):
        load_dingtalk_stream_sdk(import_module=fake_import_module)
