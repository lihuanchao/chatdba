from collections.abc import Iterator
import threading

from openai import OpenAI

from chatdba.domain.models import AgentTokenUsage


class QwenGateway:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        embedding_model: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._embedding_model = embedding_model
        self._usage_local = threading.local()

    def start_usage_collection(self, *, task_id: str) -> None:
        self._usage_local.task_id = task_id
        self._usage_local.records = []

    def finish_usage_collection(self) -> list[AgentTokenUsage]:
        records = list(getattr(self._usage_local, "records", []))
        self._usage_local.records = []
        self._usage_local.task_id = None
        return records

    def stream_report(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        usage = None
        for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            content = chunk.choices[0].delta.content
            if content:
                yield content
        self._record_usage(
            model=self._model,
            operation="stream_report",
            usage=usage,
        )

    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        self._record_usage(
            model=self._model,
            operation="generate_report",
            usage=getattr(response, "usage", None),
        )
        return str(response.choices[0].message.content)

    def embed_text(self, text: str) -> list[float]:
        if not self._embedding_model:
            raise RuntimeError("Qwen embedding model is not configured.")
        response = self._client.embeddings.create(
            model=self._embedding_model,
            input=text,
        )
        self._record_usage(
            model=self._embedding_model,
            operation="embed_text",
            usage=getattr(response, "usage", None),
        )
        return [float(value) for value in response.data[0].embedding]

    def _record_usage(
        self,
        *,
        model: str,
        operation: str,
        usage: object | None,
    ) -> None:
        task_id = getattr(self._usage_local, "task_id", None)
        records = getattr(self._usage_local, "records", None)
        if not task_id or records is None or usage is None:
            return

        prompt_tokens = _int_usage_value(usage, "prompt_tokens")
        completion_tokens = _int_usage_value(usage, "completion_tokens")
        total_tokens = _int_usage_value(usage, "total_tokens")
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

        raw_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        records.append(
            AgentTokenUsage(
                task_id=task_id,
                provider="qwen",
                model=model,
                operation=operation,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                raw_usage=raw_usage,
            )
        )


def _int_usage_value(usage: object, key: str) -> int:
    value = None
    if isinstance(usage, dict):
        value = usage.get(key)
    else:
        value = getattr(usage, key, None)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
