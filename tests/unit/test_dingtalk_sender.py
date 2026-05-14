import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.sender import (
    DingTalkCardStreamingSender,
    DingTalkSessionWebhookSender,
)


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

    with pytest.raises(RuntimeError, match="缺少 sessionWebhook"):
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


def test_card_streaming_sender_updates_single_card_instance():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.dingtalk_client = dingtalk_client
            self.incoming_message = incoming_message
            self.started = False
            self.stream_calls = []
            self.finished_markdown = None
            self.card_template_id = "default-template"
            self.card_instance_id = "card-1"

        def set_title_and_logo(self, title, logo):
            self.title = title
            self.logo = logo

        def ai_start(self):
            self.started = True

        def ai_streaming(self, markdown, append=False):
            self.stream_calls.append((markdown, append))

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            self.card_template_id = template_id
            self.created_payload = card_data
            self.callback_type = callback_type
            return "card-1"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            self.stream_calls.append(
                (card_instance_id, content_key, content_value, append, finished, failed)
            )
            return None

        def ai_finish(self, markdown=None, button_list=None, tips=""):
            self.finished_markdown = markdown

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        ai_card_status_inputing="inputing",
    )
    message = DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-1", "conversationId": "conv-1"},
        card_template_id="custom-template",
    )

    sender.send_markdown_chunk(message=message, text="# 标题\n")
    sender.send_markdown_chunk(message=message, text="正文")
    state = sender._states["msg-1"]
    sender.finish_markdown_stream(message=message, failed=False)

    assert state.card_instance.started is False
    assert state.card_instance.created_payload == {"content": ""}
    assert state.card_instance.callback_type == "STREAM"
    assert state.card_instance.card_template_id == "custom-template"
    assert state.card_instance.stream_calls == [
        ("card-1", "content", "# 标题\n", False, False, False),
        ("card-1", "content", "# 标题\n正文", False, False, False),
        ("card-1", "content", "# 标题\n正文", False, True, False),
    ]
    assert "msg-1" not in sender._states


def test_card_streaming_sender_preserves_custom_template_content_on_failed_finish():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_instance_id = "card-schema"
            self.stream_calls = []

        def set_title_and_logo(self, title, logo):
            return None

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            return "card-schema"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            self.stream_calls.append(
                (card_instance_id, content_key, content_value, append, finished, failed)
            )

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
    )
    message = DingTalkInboundMessage(
        message_id="msg-schema",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-schema", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="请补充数据库库名")
    state = sender._states["msg-schema"]
    sender.finish_markdown_stream(message=message, failed=True)

    assert state.card_instance.stream_calls[-1] == (
        "card-schema",
        "content",
        "请补充数据库库名",
        False,
        True,
        False,
    )


def test_card_streaming_sender_separates_custom_template_chunks_with_paragraph_break():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_instance_id = "card-progress"
            self.stream_calls = []

        def set_title_and_logo(self, title, logo):
            return None

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            return "card-progress"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            self.stream_calls.append(
                (card_instance_id, content_key, content_value, append, finished, failed)
            )

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
    )
    message = DingTalkInboundMessage(
        message_id="msg-progress",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-progress", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="已生成诊断结论...\n")
    sender.send_markdown_chunk(message=message, text="已生成优化报告...\n")

    state = sender._states["msg-progress"]
    assert state.card_instance.stream_calls[-1] == (
        "card-progress",
        "content",
        "已生成诊断结论... 已生成优化报告...\n",
        False,
        False,
        False,
    )


def test_card_streaming_sender_falls_back_to_session_webhook_when_no_callback_data():
    seen = {}

    def fake_opener(request):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return RecordingResponse()

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=SimpleNamespace(from_dict=lambda data: data),
        card_instance_cls=SimpleNamespace,
        opener=fake_opener,
    )
    message = DingTalkInboundMessage(
        message_id="msg-2",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
    )

    sender.send_markdown_chunk(message=message, text="fallback text")

    assert seen["url"] == "https://example.test/webhook"
    assert seen["body"]["msgtype"] == "text"
    assert seen["body"]["text"]["content"] == "fallback text"


def test_card_streaming_sender_uses_default_template_id_when_message_has_no_override():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_template_id = "sdk-default"
            self.card_instance_id = "card-2"
            self.stream_calls = []

        def set_title_and_logo(self, title, logo):
            return None

        def ai_start(self):
            return None

        def ai_streaming(self, markdown, append=False):
            self.stream_calls.append((markdown, append))

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            self.card_template_id = template_id
            self.created_payload = card_data
            self.callback_type = callback_type
            return "card-2"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            self.stream_calls.append(
                (card_instance_id, content_key, content_value, append, finished, failed)
            )
            return None

        def ai_finish(self, markdown=None, button_list=None, tips=""):
            return None

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
    )
    message = DingTalkInboundMessage(
        message_id="msg-3",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-3", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="hello")

    assert sender._states["msg-3"].card_instance.card_template_id == "env-template"
    assert sender._states["msg-3"].card_instance.created_payload == {"content": ""}
    assert sender._states["msg-3"].card_instance.callback_type == "STREAM"
    assert sender._states["msg-3"].card_instance.stream_calls == [
        ("card-2", "content", "hello", False, False, False)
    ]


def test_card_streaming_sender_uses_streaming_api_when_no_template_id():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_template_id = "sdk-default"
            self.card_instance_id = "card-4"
            self.stream_calls = []
            self.put_calls = []

        def set_title_and_logo(self, title, logo):
            return None

        def ai_start(self):
            return None

        def ai_streaming(self, markdown, append=False):
            self.stream_calls.append((markdown, append))

        def put_card_data(self, card_instance_id, card_data):
            self.put_calls.append((card_instance_id, card_data))
            return {"ok": True}

        def ai_finish(self, markdown=None, button_list=None, tips=""):
            return None

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
    )
    message = DingTalkInboundMessage(
        message_id="msg-4",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-4", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="hello")

    assert sender._states["msg-4"].card_instance.stream_calls == [("hello", True)]
    assert sender._states["msg-4"].card_instance.put_calls == []


def test_card_streaming_sender_keeps_card_mode_when_streaming_returns_none():
    seen = {}

    def fake_opener(request):
        seen.setdefault("bodies", []).append(json.loads(request.data.decode("utf-8")))
        return RecordingResponse()

    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_instance_id = "card-5"

        def set_title_and_logo(self, title, logo):
            return None

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            self.card_template_id = template_id
            return "card-5"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            return None

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
        ai_card_status_inputing="inputing",
        opener=fake_opener,
    )
    message = DingTalkInboundMessage(
        message_id="msg-5",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-5", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="fallback")
    sender.finish_markdown_stream(message=message, failed=False)

    assert seen.get("bodies", []) == []


def test_card_streaming_sender_aggregates_fallback_text_chunks_and_sends_once():
    seen = {}

    def fake_opener(request):
        seen.setdefault("bodies", []).append(json.loads(request.data.decode("utf-8")))
        return RecordingResponse()

    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_instance_id = None

        def set_title_and_logo(self, title, logo):
            return None

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            return ""

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
        opener=fake_opener,
    )
    message = DingTalkInboundMessage(
        message_id="msg-7",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-7", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="第一段")
    sender.send_markdown_chunk(message=message, text="第二段")
    sender.finish_markdown_stream(message=message, failed=False)

    assert len(seen["bodies"]) == 1
    assert seen["bodies"][0]["text"]["content"] == "第一段第二段"


def test_card_streaming_sender_prefers_custom_field_in_streaming_payload():
    class FakeChatbotMessage:
        @classmethod
        def from_dict(cls, data):
            return SimpleNamespace(data=data)

    class FakeCardInstance:
        def __init__(self, dingtalk_client, incoming_message):
            self.card_template_id = "sdk-default"
            self.card_instance_id = None
            self.create_calls = []
            self.stream_calls = []

        def set_title_and_logo(self, title, logo):
            return None

        def create_and_send_card(self, template_id, card_data, callback_type="STREAM"):
            self.create_calls.append((template_id, card_data, callback_type))
            return "card-6"

        def streaming(
            self,
            card_instance_id,
            content_key,
            content_value,
            append,
            finished,
            failed,
        ):
            self.stream_calls.append(
                (card_instance_id, content_key, content_value, append, finished, failed)
            )
            return None

    sender = DingTalkCardStreamingSender(
        dingtalk_client=object(),
        chatbot_message_cls=FakeChatbotMessage,
        card_instance_cls=FakeCardInstance,
        default_card_template_id="env-template",
        card_content_field="wrongField",
    )
    message = DingTalkInboundMessage(
        message_id="msg-6",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select 1",
        session_webhook="https://example.test/webhook",
        callback_data={"msgId": "msg-6", "conversationId": "conv-1"},
    )

    sender.send_markdown_chunk(message=message, text="hello")

    state = sender._states["msg-6"]
    assert state.card_instance.create_calls[0] == (
        "env-template",
        {"wrongField": ""},
        "STREAM",
    )
    assert state.card_instance.stream_calls[0] == (
        "card-6",
        "wrongField",
        "hello",
        False,
        False,
        False,
    )
