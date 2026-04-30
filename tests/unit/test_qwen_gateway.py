from chatdba.models.qwen_gateway import QwenGateway


class FakeChunk:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": content})()})]


class FakeStreamCompletions:
    def create(self, **kwargs):
        assert kwargs["model"] == "qwen-plus"
        assert kwargs["messages"] == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        assert kwargs["stream"] is True
        return [FakeChunk("hello"), FakeChunk(""), FakeChunk(" world")]


class FakeNonStreamChoice:
    def __init__(self, content: str):
        self.message = type("Message", (), {"content": content})()


class FakeNonStreamCompletions:
    def create(self, **kwargs):
        assert kwargs["stream"] is False
        return type("Response", (), {"choices": [FakeNonStreamChoice("{\"ok\": true}")]} )()


class FakeClient:
    chat = type("Chat", (), {"completions": FakeStreamCompletions()})()


class FakeNonStreamClient:
    chat = type("Chat", (), {"completions": FakeNonStreamCompletions()})()


class FakeEmbeddings:
    def create(self, **kwargs):
        assert kwargs["model"] == "text-embedding-v4"
        assert kwargs["input"] == "mysql order by limit"
        return type(
            "EmbeddingResponse",
            (),
            {"data": [type("EmbeddingItem", (), {"embedding": [0.12, 0.34, 0.56]})()]},
        )()


class FakeEmbeddingClient:
    embeddings = FakeEmbeddings()


def test_gateway_streams_text_chunks():
    gateway = QwenGateway(client=FakeClient(), model="qwen-plus")

    assert list(gateway.stream_report("system", "user")) == ["hello", " world"]


def test_gateway_generates_non_stream_report_text():
    gateway = QwenGateway(client=FakeNonStreamClient(), model="qwen-plus")

    assert gateway.generate_report("system", "user") == "{\"ok\": true}"


def test_gateway_generates_embeddings():
    gateway = QwenGateway(
        client=FakeEmbeddingClient(),
        model="qwen-plus",
        embedding_model="text-embedding-v4",
    )

    assert gateway.embed_text("mysql order by limit") == [0.12, 0.34, 0.56]
