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
