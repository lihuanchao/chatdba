import importlib
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_template_id_and_clean_text


class DingTalkSdkImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DingTalkSdkBundle:
    stream_module: Any
    chatbot_module: Any


class AppMessageHandler(Protocol):
    def handle(self, message: DingTalkInboundMessage):
        pass


def load_dingtalk_stream_sdk(
    *,
    import_module: Callable[[str], Any] = importlib.import_module,
) -> DingTalkSdkBundle:
    try:
        stream_module = import_module("dingtalk_stream")
        chatbot_module = import_module("dingtalk_stream.chatbot")
    except ImportError as exc:
        raise DingTalkSdkImportError(
            "dingtalk-stream is not installed. "
            "Run `pip install dingtalk-stream` before starting the DingTalk runtime."
        ) from exc

    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


class DingTalkStreamChatbotHandler:
    def __init__(self, *, handler: AppMessageHandler) -> None:
        self._handler = handler

    def handle_callback_data(self, callback_data: dict[str, Any]):
        text_payload = callback_data.get("text")
        text = ""
        if isinstance(text_payload, dict):
            text = str(text_payload.get("content", "")).strip()
        card_template_id, cleaned_text = extract_template_id_and_clean_text(text)

        message = DingTalkInboundMessage(
            message_id=str(callback_data.get("msgId", "")),
            conversation_id=str(callback_data.get("conversationId", "")),
            sender_id=str(callback_data.get("senderId", "")),
            text=cleaned_text,
            session_webhook=_as_optional_string(callback_data.get("sessionWebhook")),
            callback_data=callback_data,
            card_template_id=card_template_id,
        )
        return self._handler.handle(message)


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text:
        return text
    return None
