import json
import urllib.error
import urllib.request
from typing import Protocol


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
                f"Missing session webhook for conversation {conversation_id}"
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
