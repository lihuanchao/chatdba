from chatdba.worker.run_task import run_sql_optimization_task


def test_run_sql_optimization_task_invokes_graph_with_collector(monkeypatch):
    seen = {}

    class FakeGraph:
        def invoke(self, payload):
            seen["payload"] = payload
            return {"result": "ok"}

    def fake_build_sql_optimization_graph(*, collector):
        seen["collector"] = collector
        return FakeGraph()

    monkeypatch.setattr(
        "chatdba.worker.run_task.build_sql_optimization_graph",
        fake_build_sql_optimization_graph,
    )

    collector = object()
    task_payload = {"raw_sql": "select * from orders"}

    result = run_sql_optimization_task(task_payload, collector)

    assert result == {"result": "ok"}
    assert seen["collector"] is collector
    assert seen["payload"] == task_payload


def test_run_sql_optimization_task_emits_progress(monkeypatch):
    class FakeGraph:
        def invoke(self, payload):
            return {"result": "ok", "payload": payload}

    def fake_build_sql_optimization_graph(*, collector):
        return FakeGraph()

    monkeypatch.setattr(
        "chatdba.worker.run_task.build_sql_optimization_graph",
        fake_build_sql_optimization_graph,
    )

    events = []

    result = run_sql_optimization_task(
        {"raw_sql": "select * from orders"},
        object(),
        progress_sink=events.append,
    )

    assert result["result"] == "ok"
    assert events == ["Parsing SQL\n", "Generated diagnostic findings\n"]
