from dataclasses import dataclass
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage


class DingTalkTextSender(Protocol):
    def send_text(
        self,
        *,
        conversation_id: str,
        session_webhook: str | None,
        text: str,
    ) -> None:
        pass

    def send_markdown_chunk(
        self,
        *,
        message: DingTalkInboundMessage,
        text: str,
    ) -> None:
        pass

    def finish_markdown_stream(
        self,
        *,
        message: DingTalkInboundMessage,
        failed: bool = False,
    ) -> None:
        pass


@dataclass(frozen=True)
class DingTalkSendResult:
    conversation_id: str
    message: str
    ok: bool
    error: str | None = None


class DingTalkResponder:
    def __init__(self, sender: DingTalkTextSender) -> None:
        self._sender = sender

    def reply_text(
        self,
        message: DingTalkInboundMessage,
        text: str,
    ) -> DingTalkSendResult:
        try:
            send_chunk = getattr(self._sender, "send_markdown_chunk", None)
            if callable(send_chunk):
                send_chunk(message=message, text=text)
            else:
                self._sender.send_text(
                    conversation_id=message.conversation_id,
                    session_webhook=message.session_webhook,
                    text=text,
                )
        except Exception as exc:
            return DingTalkSendResult(
                conversation_id=message.conversation_id,
                message=text,
                ok=False,
                error=str(exc),
            )

        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )

    def finish_stream(
        self,
        message: DingTalkInboundMessage,
        *,
        failed: bool = False,
    ) -> DingTalkSendResult | None:
        finish_chunk = getattr(self._sender, "finish_markdown_stream", None)
        if not callable(finish_chunk):
            return None

        try:
            finish_chunk(message=message, failed=failed)
        except Exception as exc:
            return DingTalkSendResult(
                conversation_id=message.conversation_id,
                message="finish_stream",
                ok=False,
                error=str(exc),
            )

        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message="finish_stream",
            ok=True,
        )
