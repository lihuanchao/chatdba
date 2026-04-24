from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.service import OptimizationTaskService


def make_context() -> DingTalkContext:
    return DingTalkContext(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        session_webhook="https://example.test/webhook",
    )


def test_task_service_builds_payload_and_runs_worker():
    seen = {}

    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        seen["task_payload"] = task_payload
        seen["collector"] = collector
        seen["report_composer"] = report_composer
        seen["progress_sink"] = progress_sink
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return {"report": {"summary": "ok"}}

    progress = []
    progress_sink = progress.append
    collector = object()
    report_composer = object()
    service = OptimizationTaskService(
        collector=collector,
        report_composer=report_composer,
        task_runner=fake_runner,
        task_id_factory=lambda: "task-1",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
        progress_sink=progress_sink,
    )

    assert execution.task_id == "task-1"
    assert execution.status == TaskStatus.COMPLETED
    assert execution.result == {"report": {"summary": "ok"}}
    assert execution.error is None
    assert seen["collector"] is collector
    assert seen["report_composer"] is report_composer
    assert seen["progress_sink"] is progress_sink
    assert seen["task_payload"]["task_id"] == "task-1"
    assert seen["task_payload"]["raw_sql"] == "select * from orders"
    assert seen["task_payload"]["dingtalk"]["conversation_id"] == "conv-1"
    assert progress == ["Parsing SQL\n"]


def test_task_service_converts_runner_exception_to_failed_execution():
    def failing_runner(task_payload, collector, report_composer=None, progress_sink=None):
        raise RuntimeError("collector unavailable")

    service = OptimizationTaskService(
        collector=object(),
        task_runner=failing_runner,
        task_id_factory=lambda: "task-2",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.task_id == "task-2"
    assert execution.status == TaskStatus.FAILED
    assert execution.result is None
    assert execution.error == "collector unavailable"
