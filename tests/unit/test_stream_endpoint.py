from fastapi.testclient import TestClient

from chatdba.app.main import create_app
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
