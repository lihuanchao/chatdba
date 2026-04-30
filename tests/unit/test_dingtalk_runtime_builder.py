import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.runtime import (
    build_dingtalk_runtime,
)
from chatdba.dingtalk.channel import DingTalkInboundMessage
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


def make_sdk_bundle_with_card_streaming() -> DingTalkSdkBundle:
    class FakeAIMarkdownCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.dingtalk_client = dingtalk_client
            self.incoming_message = incoming_message

        def set_title_and_logo(self, title, logo):
            return None

        def ai_start(self):
            return None

        def ai_streaming(self, markdown, append=False):
            return None

        def ai_finish(self, markdown=None, button_list=None, tips=""):
            return None

        def ai_fail(self):
            return None

    class FakeChatbotMessageWithFromDict:
        TOPIC = "/v1.0/im/bot/messages/get"

        @classmethod
        def from_dict(cls, data):
            return data

    stream_module = SimpleNamespace(
        Credential=FakeCredential,
        DingTalkStreamClient=FakeClient,
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
        AIMarkdownCardInstance=FakeAIMarkdownCardInstance,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=FakeChatbotMessageWithFromDict)
    return DingTalkSdkBundle(stream_module=stream_module, chatbot_module=chatbot_module)


def make_settings():
    return SimpleNamespace(
        dingtalk_client_id="client-id",
        dingtalk_client_secret="client-secret",
        stream_update_interval_ms=1000,
        mysql_connect_timeout_seconds=3,
        mysql_query_timeout_seconds=8,
        metadata_mysql_host="",
        metadata_mysql_port=3306,
        metadata_mysql_user="",
        metadata_mysql_password="",
        metadata_mysql_database="",
        metadata_route_table="table_routes",
        metadata_instance_table="db_instances",
        database_url="",
        qwen_api_key="",
        qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        qwen_model="qwen-plus",
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


def test_build_runtime_uses_sql_only_collector_when_none_is_provided():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    envelope = runtime.collector.collect("select 1", [])

    assert envelope.status.value == "sql_only"
    assert envelope.collection_errors == [
        "当前未配置元数据库路由，系统将退化为 SQL-only 分析。"
    ]


def test_build_runtime_uses_routed_collector_when_metadata_settings_are_present(monkeypatch):
    seen = {}
    settings = make_settings()
    settings.metadata_mysql_host = "127.0.0.1"
    settings.metadata_mysql_user = "metadata_ro"
    settings.metadata_mysql_password = "secret"
    settings.metadata_mysql_database = "metadata"

    class FakeRoutedCollector:
        def __init__(self, *, router, connection_factory):
            seen["router"] = router
            seen["connection_factory"] = connection_factory

    monkeypatch.setattr(
        "chatdba.dingtalk.runtime.build_metadata_client",
        lambda settings: "metadata-client",
    )
    monkeypatch.setattr(
        "chatdba.dingtalk.runtime.RoutedMysqlEvidenceCollector",
        FakeRoutedCollector,
    )

    runtime = build_dingtalk_runtime(
        settings=settings,
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    assert type(runtime.collector).__name__ == "FakeRoutedCollector"
    assert seen["router"].__class__.__name__ == "MetadataRouter"


def test_registered_sdk_callback_handler_acks_even_when_routing_is_not_configured():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
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


def test_registered_sdk_callback_handler_acks_before_background_work_finishes():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )
    callback_handler = runtime.client.registrations[0][1]
    started = threading.Event()
    release = threading.Event()

    def slow_handle(message):
        started.set()
        release.wait(timeout=1)
        return SimpleNamespace(
            accepted=True,
            status=SimpleNamespace(value="completed"),
            task_id="task-slow",
            send_results=[],
        )

    runtime.app_handler.handle = slow_handle
    callback = SimpleNamespace(
        data={
            "msgId": "msg-slow",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "text",
            "text": {"content": "SQL优化 select * from orders"},
        }
    )

    start = time.monotonic()
    status, message = asyncio.run(
        asyncio.wait_for(callback_handler.process(callback), timeout=0.1)
    )
    elapsed = time.monotonic() - start

    assert status == "OK"
    assert message == "OK"
    assert elapsed < 0.1
    assert started.wait(timeout=0.2) is True
    release.set()


def test_build_runtime_defaults_to_card_streaming_sender_when_sdk_supports_it():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sdk_bundle=make_sdk_bundle_with_card_streaming(),
    )

    assert runtime.sender.__class__.__name__ == "DingTalkCardStreamingSender"


def test_build_runtime_routes_fault_diagnosis_messages_to_fault_agent():
    sender = FakeSender()
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=sender,
        sdk_bundle=make_sdk_bundle(),
    )

    result = runtime.app_handler.handle(
        DingTalkInboundMessage(
            message_id="msg-fault",
            conversation_id="conv-1",
            sender_id="user-1",
            text="故障诊断 订单系统 CPU 高，IP 10.186.17.54",
            session_webhook="https://example.test/webhook",
        )
    )

    assert result.accepted is True
    assert result.status.value == "completed"
    full_text = "\n".join(item["text"] for item in sender.messages)
    assert "数据库故障诊断任务已接收" in full_text
    assert "### 一、问题简述" in full_text
    assert "证据不足" in full_text
