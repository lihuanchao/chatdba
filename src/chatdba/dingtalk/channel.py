import re
from dataclasses import dataclass, field


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

    text = re.sub(r"@\S+", "", message.text).strip()
    prefixes = ["optimize", "sql optimize", "优化", "SQL优化"]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()
    return text


@dataclass
class StreamUpdateBuffer:
    interval_ms: int
    chunks: list[str] = field(default_factory=list)

    def add(self, chunk: str) -> None:
        self.chunks.append(chunk)

    def flush(self, force: bool = False) -> str:
        if not force and not self.chunks:
            return ""
        output = "".join(self.chunks)
        self.chunks.clear()
        return output
