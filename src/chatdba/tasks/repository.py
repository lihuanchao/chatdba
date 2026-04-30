import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.events import ProgressEvent


class TaskRepository(Protocol):
    def create_task(
        self,
        task_id: str,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None = None,
    ) -> None:
        raise NotImplementedError

    def append_event(self, event: ProgressEvent) -> None:
        raise NotImplementedError

    def get_task(self, task_id: str) -> dict[str, object]:
        raise NotImplementedError


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, object]] = {}

    def create_task(
        self,
        task_id: str,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None = None,
    ) -> None:
        self._tasks[task_id] = {
            "task_id": task_id,
            "raw_sql": raw_sql,
            "status": TaskStatus.RECEIVED,
            "dingtalk_message_id": dingtalk_context.message_id if dingtalk_context else None,
            "dingtalk_conversation_id": (
                dingtalk_context.conversation_id if dingtalk_context else None
            ),
            "events": [],
        }

    def append_event(self, event: ProgressEvent) -> None:
        task = self._tasks[event.task_id]
        task["status"] = event.status
        task["events"].append(event)

    def get_task(self, task_id: str) -> dict[str, object]:
        return self._tasks[task_id]


class PostgresTaskRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect_fn: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        self._database_url = _asyncpg_database_url(database_url)
        self._connect_fn = connect_fn

    def create_task(
        self,
        task_id: str,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None = None,
    ) -> None:
        asyncio.run(
            self._create_task_async(
                task_id=task_id,
                raw_sql=raw_sql,
                dingtalk_context=dingtalk_context,
            )
        )

    def append_event(self, event: ProgressEvent) -> None:
        asyncio.run(self._append_event_async(event))

    def get_task(self, task_id: str) -> dict[str, object]:
        return asyncio.run(self._get_task_async(task_id))

    async def _create_task_async(
        self,
        *,
        task_id: str,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None,
    ) -> None:
        connection = await self._connect()
        try:
            await connection.execute(
                """
                INSERT INTO optimization_tasks (
                    task_id,
                    raw_sql,
                    status,
                    dingtalk_message_id,
                    dingtalk_conversation_id
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (task_id) DO UPDATE SET
                    raw_sql = EXCLUDED.raw_sql,
                    status = EXCLUDED.status,
                    dingtalk_message_id = COALESCE(
                        EXCLUDED.dingtalk_message_id,
                        optimization_tasks.dingtalk_message_id
                    ),
                    dingtalk_conversation_id = COALESCE(
                        EXCLUDED.dingtalk_conversation_id,
                        optimization_tasks.dingtalk_conversation_id
                    ),
                    updated_at = now()
                """,
                task_id,
                raw_sql,
                TaskStatus.RECEIVED.value,
                dingtalk_context.message_id if dingtalk_context else None,
                dingtalk_context.conversation_id if dingtalk_context else None,
            )
        finally:
            await connection.close()

    async def _append_event_async(self, event: ProgressEvent) -> None:
        connection = await self._connect()
        try:
            await connection.execute(
                """
                INSERT INTO optimization_events (
                    task_id,
                    status,
                    message,
                    payload,
                    created_at
                ) VALUES ($1, $2, $3, $4::jsonb, $5)
                """,
                event.task_id,
                event.status.value,
                event.message,
                json.dumps(event.payload, ensure_ascii=False),
                event.created_at,
            )
            await connection.execute(
                """
                UPDATE optimization_tasks
                SET status = $2, updated_at = now()
                WHERE task_id = $1
                """,
                event.task_id,
                event.status.value,
            )
        finally:
            await connection.close()

    async def _get_task_async(self, task_id: str) -> dict[str, object]:
        connection = await self._connect()
        try:
            task_row = await connection.fetchrow(
                """
                SELECT
                    task_id,
                    raw_sql,
                    status,
                    dingtalk_message_id,
                    dingtalk_conversation_id
                FROM optimization_tasks
                WHERE task_id = $1
                """,
                task_id,
            )
            if task_row is None:
                raise KeyError(task_id)

            event_rows = await connection.fetch(
                """
                SELECT
                    task_id,
                    status,
                    message,
                    payload,
                    created_at
                FROM optimization_events
                WHERE task_id = $1
                ORDER BY created_at ASC, id ASC
                """,
                task_id,
            )
        finally:
            await connection.close()

        return {
            "task_id": str(task_row["task_id"]),
            "raw_sql": str(task_row["raw_sql"]),
            "status": TaskStatus(str(task_row["status"])),
            "dingtalk_message_id": task_row["dingtalk_message_id"],
            "dingtalk_conversation_id": task_row["dingtalk_conversation_id"],
            "events": [
                ProgressEvent(
                    task_id=str(row["task_id"]),
                    status=TaskStatus(str(row["status"])),
                    message=str(row["message"]),
                    payload=_payload_dict(row["payload"]),
                    created_at=row["created_at"],
                )
                for row in event_rows
            ],
        }

    async def _connect(self):
        if self._connect_fn is not None:
            return await self._connect_fn(self._database_url)
        import asyncpg

        return await asyncpg.connect(self._database_url)


def _asyncpg_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _payload_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}
