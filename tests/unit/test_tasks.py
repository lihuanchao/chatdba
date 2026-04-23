from chatdba.domain.models import TaskStatus
from chatdba.tasks.events import ProgressEvent
from chatdba.tasks.repository import InMemoryTaskRepository


def test_task_repository_records_progress_events():
    repo = InMemoryTaskRepository()
    repo.create_task(task_id="task-1", raw_sql="select * from orders")
    repo.append_event(ProgressEvent(task_id="task-1", status=TaskStatus.PARSING_SQL, message="Parsing SQL"))

    task = repo.get_task("task-1")

    assert task["task_id"] == "task-1"
    assert task["status"] == TaskStatus.PARSING_SQL
    assert task["events"][0].message == "Parsing SQL"
