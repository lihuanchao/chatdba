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


def test_gateway_streams_text_chunks():
    gateway = QwenGateway(client=FakeClient(), model="qwen-plus")

    assert list(gateway.stream_report("system", "user")) == ["hello", " world"]


def test_gateway_generates_non_stream_report_text():
    gateway = QwenGateway(client=FakeNonStreamClient(), model="qwen-plus")

    assert gateway.generate_report("system", "user") == "{\"ok\": true}"
