from fastapi.testclient import TestClient

from chatdba.app.main import create_app


def test_create_sql_optimization_task():
    client = TestClient(create_app())

    response = client.post("/internal/tasks/sql-optimization", json={"raw_sql": "select * from orders"})

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"]
    assert body["status"] == "received"
