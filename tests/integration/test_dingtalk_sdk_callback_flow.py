import asyncio
from types import SimpleNamespace

from chatdba.dingtalk.runtime import create_sdk_callback_handler
from chatdba.dingtalk.sdk_runtime import DingTalkSdkBundle, DingTalkStreamChatbotHandler
from chatdba.domain.models import TaskStatus


class FakeAckMessage:
    STATUS_OK = "OK"


class FakeChatbotHandler:
    pass


def make_sdk_bundle() -> DingTalkSdkBundle:
    stream_module = SimpleNamespace(
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=SimpleNamespace(TOPIC="topic"))
    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


class RecordingAppHandler:
    def __init__(self):
        self.messages = []

    def handle(self, message):
        self.messages.append(message)
        return SimpleNamespace(
            accepted=True,
            status=TaskStatus.COMPLETED,
            task_id="task-1",
        )


def test_sdk_callback_handler_processes_message_and_returns_ack():
    app_handler = RecordingAppHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=make_sdk_bundle(),
        adapter=adapter,
    )

    status, message = asyncio.run(
        callback_handler.process(
            SimpleNamespace(
                data={
                    "msgId": "msg-1",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "sessionWebhook": "https://example.test/webhook",
                    "msgtype": "text",
                    "text": {"content": "SQL优化 select * from orders"},
                }
            )
        )
    )

    assert status == "OK"
    assert message == "OK"
    assert app_handler.messages[0].conversation_id == "conv-1"
    assert app_handler.messages[0].text == "SQL优化 select * from orders"
