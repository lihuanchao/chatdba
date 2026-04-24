import asyncio
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.runtime import (
    UnsupportedMysqlCollector,
    build_dingtalk_runtime,
)
from chatdba.dingtalk.sdk_runtime import DingTalkSdkBundle


class FakeCredential:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret


class FakeClient:
    def __init__(self, credential):
        self.credential = credential
        self.registrations = []
        self.started = False

    def register_callback_handler(self, topic, handler):
        self.registrations.append((topic, handler))

    def start_forever(self):
        self.started = True


class FakeAckMessage:
    STATUS_OK = "OK"


class FakeChatbotHandler:
    pass


class FakeChatbotMessage:
    TOPIC = "/v1.0/im/bot/messages/get"


class FakeSender:
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


def make_sdk_bundle() -> DingTalkSdkBundle:
    stream_module = SimpleNamespace(
        Credential=FakeCredential,
        DingTalkStreamClient=FakeClient,
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=FakeChatbotMessage)
    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


def make_settings():
    return SimpleNamespace(
        dingtalk_client_id="client-id",
        dingtalk_client_secret="client-secret",
        stream_update_interval_ms=1000,
    )


def test_build_runtime_registers_chatbot_handler_and_starts_client():
    class FakeCollector:
        def collect(self, sql, tables):
            return {"sql": sql, "tables": tables}

    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        collector=FakeCollector(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    assert runtime.client.credential.client_id == "client-id"
    assert runtime.client.credential.client_secret == "client-secret"
    assert runtime.client.registrations[0][0] == FakeChatbotMessage.TOPIC

    runtime.start()

    assert runtime.client.started is True


def test_build_runtime_uses_explicit_fallback_collector_when_none_is_provided():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    with pytest.raises(
        RuntimeError, match="MySQL runtime collector is not configured"
    ):
        runtime.collector.collect("select 1", [])


def test_registered_sdk_callback_handler_acks_and_delegates_to_app_handler():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        collector=UnsupportedMysqlCollector(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )
    callback_handler = runtime.client.registrations[0][1]
    callback = SimpleNamespace(
        data={
            "msgId": "msg-1",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "text",
            "text": {"content": "SQL优化 select * from orders"},
        }
    )

    status, message = asyncio.run(callback_handler.process(callback))

    assert status == "OK"
    assert message == "OK"
