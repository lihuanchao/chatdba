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
    use_streaming_endpoint: bool = True
    template_id: str | None = None
    active_content_field: str = "msgContent"
    content_field_candidates: tuple[str, ...] = ("msgContent",)


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
        card_content_field: str = "msgContent",
        opener: RequestOpener | None = None,
    ) -> None:
        super().__init__(opener=opener)
        self._dingtalk_client = dingtalk_client
        self._chatbot_message_cls = chatbot_message_cls
        self._card_instance_cls = card_instance_cls
        self._card_title = card_title
        self._default_card_template_id = (default_card_template_id or "").strip() or None
        self._ai_card_status_inputing = ai_card_status_inputing
        self._card_content_field = (card_content_field or "msgContent").strip() or "msgContent"
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
                card_instance, active_content_field, content_field_candidates = (
                    self._create_card_instance(message, template_id=template_id)
                )
                state = _CardStreamState(
                    card_instance=card_instance,
                    use_streaming_endpoint=not bool(template_id),
                    template_id=template_id,
                    active_content_field=active_content_field,
                    content_field_candidates=content_field_candidates,
                )
                self._states[message.message_id] = state
                LOGGER.info(
                    "DingTalk card stream initialized: message_id=%s template_id=%s mode=%s content_field=%s",
                    message.message_id,
                    template_id or "<sdk-default>",
                    "ai_streaming" if state.use_streaming_endpoint else "card_update",
                    state.active_content_field,
                )

            state.rendered_markdown += text
            if state.use_streaming_endpoint:
                state.card_instance.ai_streaming(markdown=text, append=True)
            else:
                result = self._update_card_markdown(state, finished=False, failed=False)
                if result is None:
                    raise RuntimeError("钉钉卡片更新失败，返回空响应。")
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
            if state.use_streaming_endpoint and failed:
                state.card_instance.ai_fail()
                return

            if state.use_streaming_endpoint:
                state.card_instance.ai_finish(markdown=state.rendered_markdown)
                return

            # 自定义模板使用 put_card_data 收口，避免 flowStatus 与模板字段冲突。
            self._update_card_markdown(state, finished=True, failed=failed)
        except Exception:
            return

    def _create_card_instance(
        self,
        message: DingTalkInboundMessage,
        *,
        template_id: str | None,
    ) -> tuple[Any, str, tuple[str, ...]]:
        if not message.callback_data:
            raise RuntimeError("缺少回调数据，无法创建钉钉流式卡片。")
        incoming_message = self._chatbot_message_cls.from_dict(message.callback_data)
        card_instance = self._card_instance_cls(self._dingtalk_client, incoming_message)
        if template_id:
            setattr(card_instance, "card_template_id", template_id)
        card_instance.set_title_and_logo(self._card_title, "")

        if template_id:
            content_field_candidates = self._build_content_field_candidates()
            for content_field in content_field_candidates:
                card_instance_id = card_instance.create_and_send_card(
                    template_id,
                    {content_field: ""},
                )
                if card_instance_id:
                    setattr(card_instance, "card_instance_id", card_instance_id)
                    return card_instance, content_field, content_field_candidates

                LOGGER.warning(
                    "DingTalk custom card creation returned empty instance id: message_id=%s template_id=%s content_field=%s",
                    message.message_id,
                    template_id,
                    content_field,
                )
            raise RuntimeError("自定义模板卡片创建失败，未获取到 card_instance_id。")
        else:
            card_instance.ai_start()
            if not getattr(card_instance, "card_instance_id", None):
                raise RuntimeError("钉钉卡片初始化失败，未获取到 card_instance_id。")
        return card_instance, "msgContent", ("msgContent",)

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

    def _build_content_field_candidates(self) -> tuple[str, ...]:
        candidates: list[str] = []
        for field in (
            self._card_content_field,
            "msgContent",
            "content",
            "markdown",
            "text",
        ):
            cleaned = field.strip() if field else ""
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
        return tuple(candidates)

    def _update_card_markdown(
        self,
        state: _CardStreamState,
        *,
        finished: bool,
        failed: bool,
    ):
        markdown = state.rendered_markdown
        if failed:
            markdown = f"{markdown}\n\n> 本次任务执行失败，请查看日志后重试。"
        fields_to_try = [state.active_content_field]
        if not state.use_streaming_endpoint:
            fields_to_try.extend(
                field
                for field in state.content_field_candidates
                if field != state.active_content_field
            )

        for content_field in fields_to_try:
            card_data = {content_field: markdown}
            if (
                state.use_streaming_endpoint
                and not finished
                and not failed
                and self._ai_card_status_inputing is not None
            ):
                card_data["flowStatus"] = self._ai_card_status_inputing

            result = state.card_instance.put_card_data(
                state.card_instance.card_instance_id,
                card_data,
            )
            if result is not None:
                state.active_content_field = content_field
                return result

            if not state.use_streaming_endpoint:
                LOGGER.warning(
                    "DingTalk custom card update returned empty response: card_instance_id=%s template_id=%s content_field=%s",
                    getattr(state.card_instance, "card_instance_id", ""),
                    state.template_id,
                    content_field,
                )
        return None
