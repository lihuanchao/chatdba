from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from chatdba.alarm_binlog.models import AlarmWebhookSettings


class RequestOpener(Protocol):
    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        pass


class AlarmWebhookSender:
    def __init__(
        self,
        settings: AlarmWebhookSettings,
        *,
        opener: RequestOpener | None = None,
    ) -> None:
        self._settings = settings
        self._opener = opener or urllib.request.urlopen

    def send_markdown(self, *, title: str, markdown: str) -> None:
        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": markdown,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._settings.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            try:
                response = self._opener(request, timeout=self._settings.timeout_seconds)
            except TypeError:
                response = self._opener(request)
        except urllib.error.HTTPError as exc:
            raise _webhook_http_error(exc) from exc

        close = getattr(response, "close", None)
        if callable(close):
            close()


def _webhook_http_error(exc: urllib.error.HTTPError) -> RuntimeError:
    body = exc.read().decode("utf-8", errors="ignore").strip()
    detail = body[:200] if body else exc.reason
    return RuntimeError(f"Alarm webhook send failed with HTTP {exc.code}: {detail}")
