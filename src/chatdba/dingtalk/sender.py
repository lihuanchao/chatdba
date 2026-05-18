import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage

LOGGER = logging.getLogger(__name__)


class RequestOpener(Protocol):
    def __call__(self, request: urllib.request.Request):
        pass


class DingTalkSessionWebhookSender:
    def __init__(self, opener: RequestOpener | None = None) -> None:
        self._opener = opener or urllib.request.urlopen

    def send_text(
        self,
        *,
        conversation_id: str,
        session_webhook: str | None,
        text: str,
    ) -> None:
        if not session_webhook:
            raise RuntimeError(
                f"会话 {conversation_id} 缺少 sessionWebhook，无法回发钉钉消息。"
            )

        payload = json.dumps(
            {
                "msgtype": "text",
                "text": {"content": text},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            session_webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            response = self._opener(request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore").strip()
            detail = body[:200] if body else exc.reason
            raise RuntimeError(
                f"DingTalk webhook send failed with HTTP {exc.code}: {detail}"
            ) from exc

        close = getattr(response, "close", None)
        if callable(close):
            close()


@dataclass
class _CardStreamState:
    card_instance: Any
    rendered_markdown: str = ""
    template_id: str | None = None
    content_key: str = "content"


class DingTalkCardStreamingSender(DingTalkSessionWebhookSender):
    def __init__(
        self,
        *,
        dingtalk_client: Any,
        chatbot_message_cls: Any,
        card_instance_cls: Any,
        card_title: str = "ChatDBA SQL优化",
        default_card_template_id: str | None = None,
        ai_card_status_inputing: Any | None = None,
        card_content_field: str = "content",
        opener: RequestOpener | None = None,
    ) -> None:
        super().__init__(opener=opener)
        self._dingtalk_client = dingtalk_client
        self._chatbot_message_cls = chatbot_message_cls
        self._card_instance_cls = card_instance_cls
        self._card_title = card_title
        self._default_card_template_id = (default_card_template_id or "").strip() or None
        self._ai_card_status_inputing = ai_card_status_inputing
        self._card_content_field = (card_content_field or "content").strip() or "content"
        self._states: dict[str, _CardStreamState] = {}
        self._text_fallback_buffers: dict[str, str] = {}

    def send_markdown_chunk(self, *, message: DingTalkInboundMessage, text: str) -> None:
        if not text:
            return

        if message.message_id in self._text_fallback_buffers:
            self._text_fallback_buffers[message.message_id] += text
            return

        if not message.callback_data:
            self._send_text_fallback(message, text)
            return

        state: _CardStreamState | None = None
        try:
            state = self._states.get(message.message_id)
            if state is None:
                template_id = message.card_template_id or self._default_card_template_id
                card_instance, content_key = self._create_card_instance(
                    message, template_id=template_id
                )
                state = _CardStreamState(
                    card_instance=card_instance,
                    template_id=template_id,
                    content_key=content_key,
                )
                self._states[message.message_id] = state
                LOGGER.info(
                    "DingTalk card stream initialized: message_id=%s template_id=%s mode=%s content_key=%s",
                    message.message_id,
                    template_id or "<sdk-default>",
                    "custom_template_streaming"
                    if template_id
                    else "sdk_ai_streaming",
                    content_key,
                )

            state.rendered_markdown = _append_markdown_chunk(
                state.rendered_markdown,
                text,
            )
            if state.template_id:
                self._stream_markdown_increment(state=state, text=text)
            else:
                self._stream_markdown_increment(state=state, text=text)
        except Exception as exc:
            self._states.pop(message.message_id, None)
            fallback_text = state.rendered_markdown if state is not None else text
            self._append_text_fallback(
                message,
                fallback_text or text,
                template_id=state.template_id if state is not None else self._default_card_template_id,
                exc=exc,
            )

    def finish_markdown_stream(
        self,
        *,
        message: DingTalkInboundMessage,
        failed: bool = False,
    ) -> None:
        fallback_text = self._text_fallback_buffers.pop(message.message_id, "")
        if fallback_text:
            if failed:
                fallback_text = f"{fallback_text}\n\n> 本次任务执行失败，请查看日志后重试。"
            self._send_text_fallback(message, fallback_text)
            return

        state = self._states.pop(message.message_id, None)
        if state is None:
            return

        try:
            if state.template_id:
                if failed:
                    final_text = (
                        "\n"
                        if state.rendered_markdown
                        else "任务失败，请查看日志后重试。"
                    )
                else:
                    final_text = "\n"
                self._stream_markdown_increment(
                    state=state,
                    text=final_text,
                    finished=True,
                    failed=False,
                )
                return

            if failed:
                final_text = (
                    "\n" if state.rendered_markdown else "任务失败，请查看日志后重试。"
                )
                self._stream_markdown_increment(
                    state=state,
                    text=final_text,
                    finished=True,
                    failed=False,
                )
            else:
                self._stream_markdown_increment(
                    state=state,
                    text="\n",
                    finished=True,
                    failed=False,
                )
        except Exception:
            LOGGER.warning(
                "DingTalk finish_markdown_stream failed: message_id=%s template_id=%s",
                message.message_id,
                state.template_id or "<sdk-default>",
                exc_info=True,
            )

    def _create_card_instance(
        self,
        message: DingTalkInboundMessage,
        *,
        template_id: str | None,
    ) -> tuple[Any, str]:
        if not message.callback_data:
            raise RuntimeError("缺少回调数据，无法创建钉钉流式卡片。")

        incoming_message = self._chatbot_message_cls.from_dict(message.callback_data)
        card_instance = self._card_instance_cls(self._dingtalk_client, incoming_message)
        card_instance.set_title_and_logo(self._card_title, "")

        if template_id:
            setattr(card_instance, "card_template_id", template_id)
            content_key = self._card_content_field
            card_instance_id = card_instance.create_and_send_card(
                template_id,
                {content_key: ""},
                callback_type="STREAM",
            )
            if not card_instance_id:
                raise RuntimeError("自定义模板卡片创建失败，未获取到 card_instance_id。")
            setattr(card_instance, "card_instance_id", card_instance_id)
            return card_instance, content_key

        card_instance.ai_start()
        if not getattr(card_instance, "card_instance_id", None):
            raise RuntimeError("钉钉卡片初始化失败，未获取到 card_instance_id。")
        return card_instance, "msgContent"

    def _stream_markdown_increment(
        self,
        *,
        state: _CardStreamState,
        text: str,
        finished: bool = False,
        failed: bool = False,
    ) -> None:
        state.card_instance.streaming(
            state.card_instance.card_instance_id,
            content_key=state.content_key,
            content_value=_sanitize_stream_content(text),
            append=True,
            finished=finished,
            failed=failed,
        )

    def _send_text_fallback(self, message: DingTalkInboundMessage, text: str) -> None:
        self.send_text(
            conversation_id=message.conversation_id,
            session_webhook=message.session_webhook,
            text=text,
        )

    def _append_text_fallback(
        self,
        message: DingTalkInboundMessage,
        text: str,
        *,
        template_id: str | None,
        exc: Exception,
    ) -> None:
        existing = self._text_fallback_buffers.get(message.message_id, "")
        self._text_fallback_buffers[message.message_id] = (
            f"{existing}{text}" if existing else text
        )
        LOGGER.warning(
            "DingTalk card stream degraded to text fallback: message_id=%s template_id=%s error=%s",
            message.message_id,
            template_id or "<sdk-default>",
            str(exc) or exc.__class__.__name__,
            exc_info=True,
        )


def _append_markdown_chunk(existing: str, chunk: str) -> str:
    if (
        _ends_with_progress_status(existing)
        and _is_progress_status_chunk(chunk)
    ):
        return f"{existing.rstrip()} {chunk.lstrip()}"
    return f"{existing}{chunk}"


def _sanitize_stream_content(text: str) -> str:
    cleaned = "".join(
        char for char in text if char in "\n\r\t" or ord(char) >= 32
    )
    return cleaned or "\n"


def _ends_with_progress_status(text: str) -> bool:
    normalized = text.strip()
    return any(
        normalized.endswith(status)
        for status in {
            "正在解析 SQL...",
            "已生成诊断结论...",
            "已生成优化报告...",
        }
    )


def _is_progress_status_chunk(text: str) -> bool:
    normalized = text.strip()
    return normalized in {
        "正在解析 SQL...",
        "已生成诊断结论...",
        "已生成优化报告...",
    }
