from types import SimpleNamespace

from fastapi.testclient import TestClient

from chatdba.app.main import create_app
from chatdba.cases.repository import OptimizationCase
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus, TaskStatus
from chatdba.domain.report_schema import OptimizationReport
from chatdba.tasks.service import OptimizationTaskExecution


def _build_report() -> OptimizationReport:
    return OptimizationReport(
        task_id="task-1",
        summary="Slow order by without matching index.",
        confidence=0.9,
        confidence_label=ConfidenceLabel.HIGH,
        evidence_status=EvidenceStatus.FULL,
        missing_evidence=[],
        limitations=[],
        bottlenecks=[],
        sql_rewrites=[],
        index_recommendations=[],
        risks=[],
        validation_steps=["Run EXPLAIN FORMAT=JSON for rewritten SQL."],
        similar_cases=[],
    )


def test_v1_stream_returns_sse_events(monkeypatch):
    class FakeTaskService:
        def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
            assert raw_sql.startswith("select")
            assert dingtalk_context.conversation_id == "dingtalk-graph-stream"
            if progress_sink is not None:
                progress_sink("Parsing SQL\n")
            return OptimizationTaskExecution(
                task_id="task-1",
                status=TaskStatus.COMPLETED,
                result={"report": _build_report()},
            )

    monkeypatch.setattr("chatdba.app.main._build_task_service", lambda: FakeTaskService())
    client = TestClient(create_app())

    response = client.post(
        "/v1/stream",
        json={"input": "SQL优化\nselect * from orders where user_id = 100;"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: ready" in response.text
    assert "event: progress" in response.text
    assert "event: markdown" in response.text
    assert "event: final" in response.text
    assert "# SQL优化报告" in response.text
    assert "event: end" in response.text


def test_v1_stream_returns_usage_event_when_sql_is_missing():
    client = TestClient(create_app())

    response = client.post("/v1/stream", json={})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text
    assert "empty_sql" in response.text
    assert "event: end" in response.text


def test_v1_stream_degrades_exception_to_error_event(monkeypatch):
    class BrokenTaskService:
        def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
            raise RuntimeError("service unavailable")

    monkeypatch.setattr("chatdba.app.main._build_task_service", lambda: BrokenTaskService())
    client = TestClient(create_app())

    response = client.post("/v1/stream", json={"input": "select * from orders;"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text
    assert "service unavailable" in response.text
    assert "event: end" in response.text


def test_v1_stream_degrades_task_service_init_failure_to_report(monkeypatch):
    def broken_factory():
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr("chatdba.app.main._build_task_service", broken_factory)
    client = TestClient(create_app())

    response = client.post("/v1/stream", json={"input": "select * from orders;"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: markdown" in response.text
    assert "SQL-only" in response.text
    assert "metadata unavailable" in response.text
    assert "event: final" in response.text
    assert "event: end" in response.text


def test_v1_stream_degrades_payload_extraction_failure_to_sse(monkeypatch):
    def broken_extract(payload):
        raise RuntimeError("bad graph payload")

    monkeypatch.setattr("chatdba.app.main._extract_sql_from_payload", broken_extract)
    client = TestClient(create_app())

    response = client.post("/v1/stream", json={"input": "select * from orders;"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: ready" in response.text
    assert "event: error" in response.text
    assert "stream_init_failed" in response.text
    assert "bad graph payload" in response.text
    assert "event: end" in response.text


def test_v1_stream_reuses_task_service_between_requests(monkeypatch):
    calls = 0

    class FakeTaskService:
        def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
            return OptimizationTaskExecution(
                task_id="task-1",
                status=TaskStatus.COMPLETED,
                result={"report": _build_report()},
            )

    def factory():
        nonlocal calls
        calls += 1
        return FakeTaskService()

    monkeypatch.setattr("chatdba.app.main._build_task_service", factory)
    client = TestClient(create_app())

    first = client.post("/v1/stream", json={"input": "select * from orders;"})
    second = client.post("/v1/stream", json={"input": "select * from users;"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1


def test_v1_stream_runtime_loads_cases_from_case_library(monkeypatch):
    monkeypatch.setattr(
        "chatdba.app.main._safe_load_settings",
        lambda: SimpleNamespace(
            database_url="postgresql+asyncpg://chatdba:chatdba@localhost:5432/chatdba",
            qwen_api_key="",
            metadata_mysql_host="",
            metadata_mysql_user="",
            metadata_mysql_database="",
        ),
    )
    monkeypatch.setattr(
        "chatdba.app.main.load_optimization_cases",
        lambda database_url: [
            OptimizationCase(
                case_id="case-runtime-1",
                db_type="mysql",
                scenario_tags=["order_by"],
                case_card="runtime case card",
                quality_score=0.9,
            )
        ],
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/stream",
        json={"input": "select * from orders order by created_at desc limit 20"},
    )

    assert response.status_code == 200
    assert "## 相似案例" in response.text
    assert "case-runtime-1" in response.text
