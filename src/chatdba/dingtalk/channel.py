import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _default_clock_ms() -> float:
    return time.monotonic() * 1000


@dataclass(frozen=True)
class DingTalkInboundMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    session_webhook: str | None = None


def extract_sql_from_message(message: DingTalkInboundMessage) -> str:
    match = re.search(r"```sql\s*(.*?)```", message.text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()

    text = re.sub(r"^\s*@\S+\s+", "", message.text, count=1).strip()
    prefixes = ["optimize", "sql optimize", "优化", "SQL优化"]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()
    return text


@dataclass
class StreamUpdateBuffer:
    interval_ms: int
    clock_ms: Callable[[], float] = _default_clock_ms
    chunks: list[str] = field(default_factory=list)
    _buffer_started_ms: float | None = field(default=None, init=False, repr=False)

    def add(self, chunk: str) -> None:
        if not self.chunks:
            self._buffer_started_ms = self.clock_ms()
        self.chunks.append(chunk)

    def flush(self, force: bool = False) -> str:
        if not self.chunks:
            return ""

        if not force:
            if self._buffer_started_ms is None:
                self._buffer_started_ms = self.clock_ms()
                return ""

            elapsed_ms = self.clock_ms() - self._buffer_started_ms
            if elapsed_ms < self.interval_ms:
                return ""

        output = "".join(self.chunks)
        self.chunks.clear()
        self._buffer_started_ms = None
        return output
