from chatdba.domain.models import AgentTokenUsage, DingTalkContext, TaskStatus
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


class RecordingTaskRepository:
    def __init__(self):
        self.created_tasks = []
        self.events = []
        self.token_usages = []

    def create_task(self, task_id: str, raw_sql: str, dingtalk_context=None) -> None:
        self.created_tasks.append(
            {
                "task_id": task_id,
                "raw_sql": raw_sql,
                "dingtalk_context": dingtalk_context,
            }
        )

    def append_event(self, event) -> None:
        self.events.append(event)

    def append_token_usage(self, usage) -> None:
        self.token_usages.append(usage)


def test_fault_diagnosis_task_service_records_events_and_token_usage():
    class FakeUsageGateway:
        def __init__(self):
            self.started_tasks = []

        def start_usage_collection(self, *, task_id: str) -> None:
            self.started_tasks.append(task_id)

        def finish_usage_collection(self):
            return [
                AgentTokenUsage(
                    task_id="fault-task-usage",
                    provider="qwen",
                    model="qwen-plus",
                    operation="generate_report",
                    prompt_tokens=80,
                    completion_tokens=40,
                    total_tokens=120,
                    raw_usage={
                        "prompt_tokens": 80,
                        "completion_tokens": 40,
                        "total_tokens": 120,
                    },
                )
            ]

    def fake_runner(
        task_payload,
        *,
        top_sql_agent=None,
        metric_agent=None,
        cmdb_resolver=None,
        qwen_gateway=None,
        progress_sink=None,
    ):
        if progress_sink:
            progress_sink("在解析故障信息... 正在获取 TopSQL 和监控指标，请稍候...\n")
        return {"report": "ok"}

    repository = RecordingTaskRepository()
    gateway = FakeUsageGateway()
    service = FaultDiagnosisTaskService(
        task_runner=fake_runner,
        task_repository=repository,
        qwen_gateway=gateway,
        task_id_factory=lambda: "fault-task-usage",
    )

    execution = service.run_diagnosis(
        input_text="订单系统 CPU 高",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.COMPLETED
    assert gateway.started_tasks == ["fault-task-usage"]
    assert repository.created_tasks == [
        {
            "task_id": "fault-task-usage",
            "raw_sql": "订单系统 CPU 高",
            "dingtalk_context": make_context(),
        }
    ]
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.DIAGNOSING,
        TaskStatus.COMPLETED,
    ]
    assert [event.payload["stage"] for event in repository.events] == [
        "received",
        "diagnosing",
        "completed",
    ]
    assert all(event.payload["task_type"] == "fault_diagnosis" for event in repository.events)
    assert repository.events[0].payload["input_length"] == len("订单系统 CPU 高")
    assert repository.events[-1].payload["result_keys"] == ["report"]
    assert len(repository.token_usages) == 1
    assert repository.token_usages[0].task_id == "fault-task-usage"
    assert repository.token_usages[0].total_tokens == 120


def test_fault_diagnosis_task_service_records_evidence_diagnostics_in_completed_event():
    def fake_runner(
        task_payload,
        *,
        top_sql_agent=None,
        metric_agent=None,
        cmdb_resolver=None,
        qwen_gateway=None,
        progress_sink=None,
    ):
        return {
            "profile": {
                "missing_fields": ["business_ip"],
            },
            "top_sql": {
                "status": "failure",
                "error_message": "慢日志库查询成功，但未返回 TopSQL。",
                "diagnostics": [
                    "top_sql.no_records: 慢日志库查询成功，但未返回 TopSQL。"
                ],
            },
            "metrics": {
                "status": "failure",
                "missing_metrics": [
                    "cpu_usage: MCP 查询失败: sse timeout; HTTP 未返回数据"
                ],
                "diagnostics": [
                    "metric.cpu_usage: MCP 查询失败: sse timeout; HTTP 未返回数据"
                ],
            },
            "report": "ok",
        }

    repository = RecordingTaskRepository()
    service = FaultDiagnosisTaskService(
        task_runner=fake_runner,
        task_repository=repository,
        task_id_factory=lambda: "fault-task-diagnostics",
    )

    execution = service.run_diagnosis(
        input_text="订单系统 CPU 高",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.COMPLETED
    completed_payload = repository.events[-1].payload
    assert completed_payload["evidence_diagnostics"] == {
        "profile_missing_fields": ["business_ip"],
        "top_sql": ["top_sql.no_records: 慢日志库查询成功，但未返回 TopSQL。"],
        "metrics": ["metric.cpu_usage: MCP 查询失败: sse timeout; HTTP 未返回数据"],
        "missing_metrics": ["cpu_usage: MCP 查询失败: sse timeout; HTTP 未返回数据"],
        "top_sql_error": "慢日志库查询成功，但未返回 TopSQL。",
        "metric_error": None,
    }


def test_fault_diagnosis_task_service_records_failed_event_on_error():
    def failing_runner(*args, **kwargs):
        raise RuntimeError("metric unavailable")

    repository = RecordingTaskRepository()
    service = FaultDiagnosisTaskService(
        task_runner=failing_runner,
        task_repository=repository,
        task_id_factory=lambda: "fault-task-failed",
    )

    execution = service.run_diagnosis(
        input_text="订单系统 CPU 高",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.FAILED
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.FAILED,
    ]
    assert repository.events[-1].payload == {
        "task_type": "fault_diagnosis",
        "stage": "failed",
        "status": "failed",
        "error": "metric unavailable",
    }
