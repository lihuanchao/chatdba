from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.fault_service import FaultDiagnosisTaskService


def make_context() -> DingTalkContext:
    return DingTalkContext(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        session_webhook="https://example.test/webhook",
    )


def test_fault_diagnosis_task_service_runs_worker_and_returns_completed_execution():
    seen = {}

    def fake_runner(
        task_payload,
        *,
        top_sql_agent=None,
        metric_agent=None,
        cmdb_resolver=None,
        qwen_gateway=None,
        progress_sink=None,
    ):
        seen["task_payload"] = task_payload
        seen["top_sql_agent"] = top_sql_agent
        seen["metric_agent"] = metric_agent
        seen["cmdb_resolver"] = cmdb_resolver
        seen["qwen_gateway"] = qwen_gateway
        if progress_sink:
            progress_sink("正在解析故障信息...\n")
        return {"report": "ok"}

    progress = []
    service = FaultDiagnosisTaskService(
        top_sql_agent="top-sql-agent",
        metric_agent="metric-agent",
        cmdb_resolver="cmdb-resolver",
        qwen_gateway="qwen",
        task_runner=fake_runner,
        task_id_factory=lambda: "fault-task-1",
    )

    execution = service.run_diagnosis(
        input_text="订单系统 CPU 高",
        dingtalk_context=make_context(),
        progress_sink=progress.append,
    )

    assert execution.task_id == "fault-task-1"
    assert execution.status == TaskStatus.COMPLETED
    assert execution.result == {"report": "ok"}
    assert seen["task_payload"]["task_id"] == "fault-task-1"
    assert seen["task_payload"]["input_text"] == "订单系统 CPU 高"
    assert seen["top_sql_agent"] == "top-sql-agent"
    assert seen["metric_agent"] == "metric-agent"
    assert seen["cmdb_resolver"] == "cmdb-resolver"
    assert seen["qwen_gateway"] == "qwen"
    assert progress == ["正在解析故障信息...\n"]


def test_fault_diagnosis_task_service_converts_runner_error_to_failed_execution():
    def failing_runner(*args, **kwargs):
        raise RuntimeError("metric unavailable")

    service = FaultDiagnosisTaskService(
        task_runner=failing_runner,
        task_id_factory=lambda: "fault-task-2",
    )

    execution = service.run_diagnosis(
        input_text="订单系统 CPU 高",
        dingtalk_context=make_context(),
    )

    assert execution.task_id == "fault-task-2"
    assert execution.status == TaskStatus.FAILED
    assert execution.error == "metric unavailable"
