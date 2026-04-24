from collections.abc import Callable

from chatdba.dingtalk.channel import DingTalkInboundMessage, StreamUpdateBuffer
from chatdba.dingtalk.responder import DingTalkResponder, DingTalkSendResult


class StreamingProgressBridge:
    def __init__(
        self,
        *,
        responder: DingTalkResponder,
        message: DingTalkInboundMessage,
        interval_ms: int,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        if clock_ms is None:
            self._buffer = StreamUpdateBuffer(interval_ms=interval_ms)
        else:
            self._buffer = StreamUpdateBuffer(
                interval_ms=interval_ms,
                clock_ms=clock_ms,
            )
        self._responder = responder
        self._message = message
        self.send_results: list[DingTalkSendResult] = []

    def emit(self, chunk: str) -> None:
        self._buffer.add(chunk)
        self._flush(force=False)

    def emit_now(self, chunk: str) -> None:
        self._buffer.add(chunk)
        self._flush(force=True)

    def finish(self) -> None:
        self._flush(force=True)

    def _flush(self, *, force: bool) -> None:
        text = self._buffer.flush(force=force)
        if not text:
            return
        result = self._responder.reply_text(self._message, text)
        self.send_results.append(result)
