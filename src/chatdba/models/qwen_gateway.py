from collections.abc import Iterator

from openai import OpenAI


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

    def stream_report(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        return str(response.choices[0].message.content)

    def embed_text(self, text: str) -> list[float]:
        if not self._embedding_model:
            raise RuntimeError("Qwen embedding model is not configured.")
        response = self._client.embeddings.create(
            model=self._embedding_model,
            input=text,
        )
        return [float(value) for value in response.data[0].embedding]
