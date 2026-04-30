from fastapi.testclient import TestClient

from chatdba.app.main import create_app


def test_create_sql_optimization_task():
    class FakeTaskService:
        def __init__(self):
            self.calls = []

        def create_task_record(self, *, raw_sql: str) -> str:
            self.calls.append(raw_sql)
            return "task-created-1"

    app = create_app()
    fake_service = FakeTaskService()
    app.state.task_service_provider.get = lambda: fake_service
    client = TestClient(app)

    response = client.post("/internal/tasks/sql-optimization", json={"raw_sql": "select * from orders"})

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task-created-1"
    assert body["status"] == "received"
    assert fake_service.calls == ["select * from orders"]
