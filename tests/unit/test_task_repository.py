from chatdba.domain.models import AgentTokenUsage, DingTalkContext, TaskStatus
from chatdba.tasks.events import ProgressEvent
from chatdba.tasks.repository import PostgresTaskRepository


class FakeAsyncConnection:
    def __init__(self, *, task_row=None, event_rows=None):
        self.execute_calls = []
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.closed = False
        self.task_row = task_row
        self.event_rows = event_rows or []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.task_row

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.event_rows

    async def close(self):
        self.closed = True


def test_postgres_task_repository_writes_task_and_event_rows():
    connections = []

    async def fake_connect(database_url: str):
        connection = FakeAsyncConnection()
        connections.append((database_url, connection))
        return connection

    repository = PostgresTaskRepository(
        "postgresql+asyncpg://chatdba:chatdba@localhost:5432/chatdba",
        connect_fn=fake_connect,
    )

    repository.create_task(
        "task-1",
        "select * from orders",
        DingTalkContext(
            message_id="msg-1",
            conversation_id="conv-1",
            sender_id="user-1",
        ),
    )
    repository.append_event(
        ProgressEvent(
            task_id="task-1",
            status=TaskStatus.PARSING_SQL,
            message="正在解析 SQL...",
        )
    )

    assert connections[0][0] == "postgresql://chatdba:chatdba@localhost:5432/chatdba"
    assert "INSERT INTO optimization_tasks" in connections[0][1].execute_calls[0][0]
    assert connections[0][1].closed is True
    assert "INSERT INTO optimization_events" in connections[1][1].execute_calls[0][0]
    assert "UPDATE optimization_tasks" in connections[1][1].execute_calls[1][0]
    assert connections[1][1].closed is True


def test_postgres_task_repository_reads_task_and_event_rows():
    async def fake_connect(database_url: str):
        return FakeAsyncConnection(
            task_row={
                "task_id": "task-1",
                "raw_sql": "select * from orders",
                "status": "completed",
                "dingtalk_message_id": "msg-1",
                "dingtalk_conversation_id": "conv-1",
            },
            event_rows=[
                {
                    "task_id": "task-1",
                    "status": "received",
                    "message": "任务已接收",
                    "payload": {},
                    "created_at": "2026-04-30T00:00:00+00:00",
                }
            ],
        )

    repository = PostgresTaskRepository(
        "postgresql+asyncpg://chatdba:chatdba@localhost:5432/chatdba",
        connect_fn=fake_connect,
    )

    task = repository.get_task("task-1")

    assert task["task_id"] == "task-1"
    assert task["status"] == TaskStatus.COMPLETED
    assert task["events"][0].status == TaskStatus.RECEIVED
    assert task["events"][0].message == "任务已接收"


def test_postgres_task_repository_writes_agent_token_usage_rows():
    connections = []

    async def fake_connect(database_url: str):
        connection = FakeAsyncConnection()
        connections.append((database_url, connection))
        return connection

    repository = PostgresTaskRepository(
        "postgresql+asyncpg://chatdba:chatdba@localhost:5432/chatdba",
        connect_fn=fake_connect,
    )

    repository.append_token_usage(
        AgentTokenUsage(
            task_id="task-usage-1",
            provider="qwen",
            model="qwen-plus",
            operation="generate_report",
            prompt_tokens=120,
            completion_tokens=64,
            total_tokens=184,
            raw_usage={
                "prompt_tokens": 120,
                "completion_tokens": 64,
                "total_tokens": 184,
            },
        )
    )

    assert "INSERT INTO agent_token_usage" in connections[0][1].execute_calls[0][0]
    assert connections[0][1].execute_calls[0][1][0] == "task-usage-1"
    assert connections[0][1].closed is True
