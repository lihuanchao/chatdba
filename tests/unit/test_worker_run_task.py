from chatdba.worker.run_task import run_sql_optimization_task


def test_run_sql_optimization_task_invokes_graph_with_collector(monkeypatch):
    seen = {}

    class FakeGraph:
        def invoke(self, payload):
            seen["payload"] = payload
            return {"result": "ok"}

    def fake_build_sql_optimization_graph(*, collector, report_composer=None):
        seen["collector"] = collector
        seen["report_composer"] = report_composer
        return FakeGraph()

    monkeypatch.setattr(
        "chatdba.worker.run_task.build_sql_optimization_graph",
        fake_build_sql_optimization_graph,
    )

    collector = object()
    task_payload = {"raw_sql": "select * from orders"}

    report_composer = object()
    result = run_sql_optimization_task(
        task_payload,
        collector,
        report_composer=report_composer,
    )

    assert result == {"result": "ok"}
    assert seen["collector"] is collector
    assert seen["report_composer"] is report_composer
    assert seen["payload"] == task_payload


def test_run_sql_optimization_task_emits_progress(monkeypatch):
    class FakeGraph:
        def invoke(self, payload):
            return {"findings": [], "report": {"summary": "ok"}, "payload": payload}

    def fake_build_sql_optimization_graph(*, collector, report_composer=None):
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

    assert result["report"] == {"summary": "ok"}
    assert "".join(events) == "正在解析 SQL... 已生成诊断结论... 已生成优化报告...\n\n"
    assert events == [
        "正在解析 SQL... ",
        "已生成诊断结论... ",
        "已生成优化报告...\n\n",
    ]


def test_run_sql_optimization_task_skips_report_progress_when_graph_stops_early(monkeypatch):
    class FakeGraph:
        def invoke(self, payload):
            return {"evidence": "needs schema"}

    def fake_build_sql_optimization_graph(*, collector, report_composer=None):
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

    assert result == {"evidence": "needs schema"}
    assert events == ["正在解析 SQL... "]
