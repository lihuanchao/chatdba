from chatdba.domain.models import TaskStatus
from chatdba.tasks.events import ProgressEvent


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, object]] = {}

    def create_task(self, task_id: str, raw_sql: str) -> None:
        self._tasks[task_id] = {
            "task_id": task_id,
            "raw_sql": raw_sql,
            "status": TaskStatus.RECEIVED,
            "events": [],
        }

    def append_event(self, event: ProgressEvent) -> None:
        task = self._tasks[event.task_id]
        task["status"] = event.status
        task["events"].append(event)

    def get_task(self, task_id: str) -> dict[str, object]:
        return self._tasks[task_id]
