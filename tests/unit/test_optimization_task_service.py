from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus
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
    assert callable(seen["progress_sink"])
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


def test_task_service_persists_task_and_progress_events():
    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        if progress_sink:
            progress_sink("正在解析 SQL...\n")
            progress_sink("已生成诊断结论...\n")
            progress_sink("已生成优化报告...\n")
        return {"report": {"summary": "ok"}}

    repository = RecordingTaskRepository()
    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_repository=repository,
        task_id_factory=lambda: "task-3",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.COMPLETED
    assert repository.created_tasks[0]["task_id"] == "task-3"
    assert repository.created_tasks[0]["raw_sql"] == "select * from orders"
    assert repository.created_tasks[0]["dingtalk_context"].conversation_id == "conv-1"
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.PARSING_SQL,
        TaskStatus.DIAGNOSING,
        TaskStatus.GENERATING_REPORT,
        TaskStatus.COMPLETED,
    ]


def test_task_service_persists_case_retrieval_debug_event_after_success():
    class FakeReportComposer:
        last_case_retrieval_debug = {
            "query": {
                "db_type": "mysql",
                "db_version_major": "8.0",
                "sql_type": "select",
                "scenario_tags": ["where_filter", "equality_predicate"],
                "plan_symptom_tags": ["index_not_used"],
                "root_cause_tags": ["implicit_cast"],
            },
            "matched_cases": [
                {
                    "case_id": "implicit-case",
                    "reason": "根因标签命中：implicit_cast；案例摘要：隐式转换案例",
                }
            ],
        }

    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        return {"report": {"summary": "ok"}}

    repository = RecordingTaskRepository()
    service = OptimizationTaskService(
        collector=object(),
        report_composer=FakeReportComposer(),
        task_runner=fake_runner,
        task_repository=repository,
        task_id_factory=lambda: "task-debug",
    )

    execution = service.run_sql(
        raw_sql="select * from users where user_name = 123",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.COMPLETED
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.RETRIEVING_CASES,
        TaskStatus.COMPLETED,
    ]
    assert repository.events[1].message == "案例检索调试信息"
    assert repository.events[1].payload == {
        "case_retrieval": FakeReportComposer.last_case_retrieval_debug
    }


def test_task_service_persists_failed_event_when_runner_errors():
    repository = RecordingTaskRepository()

    def failing_runner(task_payload, collector, report_composer=None, progress_sink=None):
        raise RuntimeError("collector unavailable")

    service = OptimizationTaskService(
        collector=object(),
        task_runner=failing_runner,
        task_repository=repository,
        task_id_factory=lambda: "task-4",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.FAILED
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.FAILED,
    ]


def test_task_service_persists_agent_token_usage_records():
    class FakeUsageGateway:
        def __init__(self):
            self.started_tasks = []

        def start_usage_collection(self, *, task_id: str) -> None:
            self.started_tasks.append(task_id)

        def finish_usage_collection(self):
            from chatdba.domain.models import AgentTokenUsage

            return [
                AgentTokenUsage(
                    task_id="task-usage",
                    provider="qwen",
                    model="qwen-plus",
                    operation="generate_report",
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    raw_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                )
            ]

    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        return {"report": {"summary": "ok"}}

    repository = RecordingTaskRepository()
    gateway = FakeUsageGateway()
    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_repository=repository,
        qwen_gateway=gateway,
        task_id_factory=lambda: "task-usage",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.COMPLETED
    assert gateway.started_tasks == ["task-usage"]
    assert len(repository.token_usages) == 1
    assert repository.token_usages[0].task_id == "task-usage"
    assert repository.token_usages[0].total_tokens == 150


def test_task_service_returns_failed_execution_when_schema_name_is_required():
    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        return {
            "evidence": EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["以下表名在元数据库中存在重复，请补充库名后重试：orders"],
            )
        }

    repository = RecordingTaskRepository()
    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_repository=repository,
        task_id_factory=lambda: "task-schema",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.FAILED
    assert execution.result is not None
    assert "请补充库名" in execution.error
    assert [event.status for event in repository.events] == [
        TaskStatus.RECEIVED,
        TaskStatus.FAILED,
    ]


def test_task_service_returns_failed_execution_when_route_spans_multiple_instances():
    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        return {
            "evidence": EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"
                ],
            )
        }

    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_id_factory=lambda: "task-multi-instance",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.status == TaskStatus.FAILED
    assert execution.result is not None
    assert "多个源实例" in execution.error
