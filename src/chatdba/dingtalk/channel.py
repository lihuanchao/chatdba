import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TEMPLATE_ID_LINE_PATTERN = re.compile(
    r"(?im)^\s*(?:模板ID|template_id)\s*[:=]\s*([a-zA-Z0-9._-]+)\s*$"
)


def _default_clock_ms() -> float:
    return time.monotonic() * 1000


@dataclass(frozen=True)
class DingTalkInboundMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    session_webhook: str | None = None
    callback_data: dict[str, Any] | None = None
    card_template_id: str | None = None


def extract_sql_from_message(message: DingTalkInboundMessage) -> str:
    _, cleaned_text = extract_template_id_and_clean_text(message.text)
    match = re.search(r"```sql\s*(.*?)```", cleaned_text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()

    text = re.sub(r"^\s*@\S+\s+", "", cleaned_text, count=1).strip()
    prefixes = ["optimize", "sql optimize", "优化", "SQL优化"]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()
    return text


def extract_template_id_and_clean_text(text: str) -> tuple[str | None, str]:
    match = TEMPLATE_ID_LINE_PATTERN.search(text)
    template_id = match.group(1) if match else None
    cleaned = TEMPLATE_ID_LINE_PATTERN.sub("", text, count=1).strip()
    return template_id, cleaned


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

        output = _join_stream_chunks(self.chunks)
        self.chunks.clear()
        self._buffer_started_ms = None
        return output


def _join_stream_chunks(chunks: list[str]) -> str:
    output = ""
    for chunk in chunks:
        if _ends_with_sql_progress_status(output) and _is_sql_progress_status_chunk(chunk):
            output = f"{output.rstrip()} {chunk.lstrip()}"
            continue
        output += chunk
    return output


def _ends_with_sql_progress_status(text: str) -> bool:
    normalized = text.strip()
    return any(
        normalized.endswith(status)
        for status in {
            "正在解析 SQL...",
            "已生成诊断结论...",
            "已生成优化报告...",
        }
    )


def _is_sql_progress_status_chunk(text: str) -> bool:
    normalized = text.strip()
    return normalized in {
        "正在解析 SQL...",
        "已生成诊断结论...",
        "已生成优化报告...",
    }
