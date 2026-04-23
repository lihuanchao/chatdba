from chatdba.models.qwen_gateway import QwenGateway


class FakeChunk:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": content})()})]


class FakeCompletions:
    def create(self, **kwargs):
        assert kwargs["model"] == "qwen-plus"
        assert kwargs["messages"] == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        assert kwargs["stream"] is True
        return [FakeChunk("hello"), FakeChunk(""), FakeChunk(" world")]


class FakeClient:
    chat = type("Chat", (), {"completions": FakeCompletions()})()


def test_gateway_streams_text_chunks():
    gateway = QwenGateway(client=FakeClient(), model="qwen-plus")

    assert list(gateway.stream_report("system", "user")) == ["hello", " world"]
