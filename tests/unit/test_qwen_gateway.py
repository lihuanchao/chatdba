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


class FakeUsage:
    def __init__(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeNonStreamCompletions:
    def create(self, **kwargs):
        assert kwargs["stream"] is False
        return type(
            "Response",
            (),
            {
                "choices": [FakeNonStreamChoice("{\"ok\": true}")],
                "usage": FakeUsage(
                    prompt_tokens=24,
                    completion_tokens=12,
                    total_tokens=36,
                ),
            },
        )()


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
            {
                "data": [type("EmbeddingItem", (), {"embedding": [0.12, 0.34, 0.56]})()],
                "usage": FakeUsage(prompt_tokens=8, completion_tokens=0, total_tokens=8),
            },
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


def test_gateway_collects_generate_report_usage_records():
    gateway = QwenGateway(client=FakeNonStreamClient(), model="qwen-plus")

    gateway.start_usage_collection(task_id="task-1")
    gateway.generate_report("system", "user")
    records = gateway.finish_usage_collection()

    assert len(records) == 1
    assert records[0].task_id == "task-1"
    assert records[0].operation == "generate_report"
    assert records[0].model == "qwen-plus"
    assert records[0].prompt_tokens == 24
    assert records[0].completion_tokens == 12
    assert records[0].total_tokens == 36


def test_gateway_records_custom_usage_operation_for_generate_report():
    gateway = QwenGateway(client=FakeNonStreamClient(), model="qwen-plus")

    gateway.start_usage_collection(task_id="task-custom")
    with gateway.usage_operation("sql_problem_profile"):
        gateway.generate_report("system", "user")
    records = gateway.finish_usage_collection()

    assert len(records) == 1
    assert records[0].operation == "sql_problem_profile"


def test_gateway_collects_embedding_usage_records():
    gateway = QwenGateway(
        client=FakeEmbeddingClient(),
        model="qwen-plus",
        embedding_model="text-embedding-v4",
    )

    gateway.start_usage_collection(task_id="task-2")
    gateway.embed_text("mysql order by limit")
    records = gateway.finish_usage_collection()

    assert len(records) == 1
    assert records[0].task_id == "task-2"
    assert records[0].operation == "embed_text"
    assert records[0].model == "text-embedding-v4"
    assert records[0].prompt_tokens == 8
    assert records[0].completion_tokens == 0
    assert records[0].total_tokens == 8


def test_gateway_records_custom_usage_operation_for_embedding():
    gateway = QwenGateway(
        client=FakeEmbeddingClient(),
        model="qwen-plus",
        embedding_model="text-embedding-v4",
    )

    gateway.start_usage_collection(task_id="task-embedding")
    with gateway.usage_operation("case_embedding_retrieval"):
        gateway.embed_text("mysql order by limit")
    records = gateway.finish_usage_collection()

    assert len(records) == 1
    assert records[0].operation == "case_embedding_retrieval"
