from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    FAULT_DIAGNOSIS_STARTED_MESSAGE,
    SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX,
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_USAGE_MESSAGE,
    DingTalkChatDBAHandler,
    DingTalkFaultDiagnosisHandler,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkSendResult
from chatdba.domain.fault_diagnosis import (
    FaultDiagnosisProfile,
    FaultDiagnosisReport,
    MetricEvidence,
    TopSqlEvidence,
)
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus, TaskStatus
from chatdba.domain.report_schema import OptimizationReport
from chatdba.tasks.service import OptimizationTaskExecution


class RecordingResponder:
    def __init__(self):
        self.messages = []
        self.finished = []

    def reply_text(self, message, text):
        self.messages.append(text)
        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )

    def finish_stream(self, message, *, failed=False):
        self.finished.append(failed)
        return None


class SuccessfulTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(
            {
                "raw_sql": raw_sql,
                "dingtalk_context": dingtalk_context,
                "progress_sink": progress_sink,
            }
        )
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return OptimizationTaskExecution(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            result={
                "report": OptimizationReport.model_validate(
                    {
                        "task_id": "task-1",
                        "summary": "Use an index to avoid filesort.",
                        "confidence": 0.35,
                        "confidence_label": "low",
                        "evidence_status": "sql_only",
                        "missing_evidence": [
                            "route_info",
                            "explain_json",
                            "create_table",
                        ],
                        "limitations": [
                            "未获取到源库执行证据，报告基于 SQL 文本、规则和历史案例生成。"
                        ],
                        "bottlenecks": [
                            {
                                "code": "limit_with_order_by",
                                "evidence": "ORDER BY with LIMIT may require a supporting index.",
                            }
                        ],
                        "sql_rewrites": [],
                        "index_recommendations": [],
                        "risks": [],
                        "validation_steps": [
                            "先在目标库测试环境执行 EXPLAIN 与回归测试，再决定是否上线。"
                        ],
                        "similar_cases": [],
                    }
                )
            },
        )


class FailedTaskService:
    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        return OptimizationTaskExecution(
            task_id="task-2",
            status=TaskStatus.FAILED,
            error="collector unavailable",
        )


class AmbiguousTableTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(raw_sql)
        if "shop.orders" in raw_sql or "`international-base`.orders" in raw_sql:
            return OptimizationTaskExecution(
                task_id=f"task-{len(self.calls)}",
                status=TaskStatus.COMPLETED,
                result={
                    "report": OptimizationReport.model_validate(
                        {
                            "task_id": f"task-{len(self.calls)}",
                            "summary": "Use an index.",
                            "confidence": 0.9,
                            "confidence_label": "high",
                            "evidence_status": "full",
                            "missing_evidence": [],
                            "limitations": [],
                            "bottlenecks": [],
                            "sql_rewrites": [],
                            "index_recommendations": [],
                            "risks": [],
                            "validation_steps": [],
                            "similar_cases": [],
                        }
                    )
                },
            )
        return OptimizationTaskExecution(
            task_id=f"task-{len(self.calls)}",
            status=TaskStatus.COMPLETED,
            result={
                "report": OptimizationReport.model_validate(
                    {
                        "task_id": f"task-{len(self.calls)}",
                        "summary": "当前为 SQL-only 分析。",
                        "confidence": 0.35,
                        "confidence_label": "low",
                        "evidence_status": "sql_only",
                        "missing_evidence": [
                            "route_info",
                            "explain_json",
                            "create_table",
                        ],
                        "limitations": [
                            "以下表名在元数据库中存在重复，请补充库名后重试：orders"
                        ],
                        "bottlenecks": [],
                        "sql_rewrites": [],
                        "index_recommendations": [],
                        "risks": [],
                        "validation_steps": [],
                        "similar_cases": [],
                    }
                )
            },
        )


class FailedAmbiguousTableTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(raw_sql)
        if "shop.orders" in raw_sql:
            return OptimizationTaskExecution(
                task_id=f"task-{len(self.calls)}",
                status=TaskStatus.COMPLETED,
                result={
                    "report": OptimizationReport.model_validate(
                        {
                            "task_id": f"task-{len(self.calls)}",
                            "summary": "Use an index.",
                            "confidence": 0.9,
                            "confidence_label": "high",
                            "evidence_status": "full",
                            "missing_evidence": [],
                            "limitations": [],
                            "bottlenecks": [],
                            "sql_rewrites": [],
                            "index_recommendations": [],
                            "risks": [],
                            "validation_steps": [],
                            "similar_cases": [],
                        }
                    )
                },
            )
        return OptimizationTaskExecution(
            task_id=f"task-{len(self.calls)}",
            status=TaskStatus.FAILED,
            error="以下表名在元数据库中存在重复，请补充库名后重试：orders",
        )


class MultiInstanceRouteTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(raw_sql)
        if "shop.orders" in raw_sql:
            return OptimizationTaskExecution(
                task_id="task-multi-instance-ok",
                status=TaskStatus.COMPLETED,
                result={
                    "report": OptimizationReport.model_validate(
                        {
                            "task_id": "task-multi-instance-ok",
                            "summary": "Use an index.",
                            "confidence": 0.9,
                            "confidence_label": "high",
                            "evidence_status": "full",
                            "missing_evidence": [],
                            "limitations": [],
                            "bottlenecks": [],
                            "sql_rewrites": [],
                            "index_recommendations": [],
                            "risks": [],
                            "validation_steps": [],
                            "similar_cases": [],
                        }
                    )
                },
            )
        return OptimizationTaskExecution(
            task_id="task-multi-instance",
            status=TaskStatus.FAILED,
            error="SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。",
        )


class MultiTableSchemaRouteTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(raw_sql)
        if "shop.orders" in raw_sql and "shop.users" in raw_sql:
            return OptimizationTaskExecution(
                task_id="task-join-schema-ok",
                status=TaskStatus.COMPLETED,
                result={
                    "report": OptimizationReport.model_validate(
                        {
                            "task_id": "task-join-schema-ok",
                            "summary": "Use an index.",
                            "confidence": 0.9,
                            "confidence_label": "high",
                            "evidence_status": "full",
                            "missing_evidence": [],
                            "limitations": [],
                            "bottlenecks": [],
                            "sql_rewrites": [],
                            "index_recommendations": [],
                            "risks": [],
                            "validation_steps": [],
                            "similar_cases": [],
                        }
                    )
                },
            )
        return OptimizationTaskExecution(
            task_id="task-join-schema",
            status=TaskStatus.FAILED,
            error="SQL 多表关联无法唯一确定数据库，请补充库名后重试：orders, users",
        )


class SuccessfulFaultTaskService:
    def __init__(self):
        self.calls = []

    def run_diagnosis(self, *, input_text, dingtalk_context, progress_sink=None):
        self.calls.append(
            {
                "input_text": input_text,
                "dingtalk_context": dingtalk_context,
                "progress_sink": progress_sink,
            }
        )
        if progress_sink:
            progress_sink("正在生成故障诊断报告...\n")
        profile = FaultDiagnosisProfile(
            input_text=input_text,
            system_name="订单系统",
            primary_ip="10.186.17.54",
            start_time="2026-04-30 14:00:00",
            end_time="2026-04-30 15:00:00",
            query_background="订单系统故障诊断",
        )
        return OptimizationTaskExecution(
            task_id="fault-1",
            status=TaskStatus.COMPLETED,
            result={
                "report": FaultDiagnosisReport(
                    task_id="fault-1",
                    summary="CPU 高",
                    markdown="### 一、问题简述\n订单系统 CPU 高\n\n### 四、问题分析及优化建议\n建议优化 TopSQL",
                    root_cause="TopSQL 导致 CPU 高",
                    recommendations=["优化 TopSQL"],
                    profile=profile,
                    top_sql=TopSqlEvidence(status="failure"),
                    metrics=MetricEvidence(status="failure"),
                )
            },
        )


def make_message(text: str) -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text=text,
        session_webhook="https://example.test/webhook",
    )


def test_handler_sends_usage_guidance_for_empty_sql():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化"))

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert responder.messages == [SQL_OPTIMIZATION_USAGE_MESSAGE]
    assert service.calls == []


def test_handler_runs_task_and_sends_start_progress_and_report():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-1"
    assert result.status == TaskStatus.COMPLETED
    assert service.calls[0]["raw_sql"] == "select * from orders"
    assert service.calls[0]["dingtalk_context"].conversation_id == "conv-1"
    assert responder.messages[0] == SQL_OPTIMIZATION_STARTED_MESSAGE
    full_stream_text = "".join(responder.messages[1:])
    assert "Parsing SQL" in full_stream_text
    assert "# SQL优化报告" in full_stream_text
    assert "## SQL重写建议" in full_stream_text
    assert "## 索引推荐" in full_stream_text


def test_handler_accepts_schema_prefixed_sql_message():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(
        make_message("SQL优化 zqsoft_mom_wms_istorage_lw SELECT count(*) FROM wmsoutputdetail")
    )

    assert result.accepted is True
    assert service.calls[0]["raw_sql"] == (
        "zqsoft_mom_wms_istorage_lw SELECT count(*) FROM wmsoutputdetail"
    )
    assert "# SQL优化报告" in "".join(responder.messages)


def test_handler_sends_failure_message_when_task_fails():
    responder = RecordingResponder()
    handler = DingTalkSqlOptimizationHandler(
        task_service=FailedTaskService(),
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-2"
    assert result.status == TaskStatus.FAILED
    assert result.error == "collector unavailable"
    assert responder.messages[-1] == (
        f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}collector unavailable"
    )


def test_handler_prompts_for_schema_and_skips_sql_only_report_when_table_is_ambiguous():
    responder = RecordingResponder()
    service = AmbiguousTableTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert service.calls == ["select * from orders"]
    assert "请补充数据库库名后继续分析" in responder.messages[-1]
    assert "orders" in responder.messages[-1]
    assert "# SQL优化报告" not in "".join(responder.messages)
    assert responder.finished[-1] is False


def test_handler_reuses_previous_sql_when_user_replies_with_schema_name():
    responder = RecordingResponder()
    service = AmbiguousTableTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(make_message("SQL优化 select * from orders"))
    second = handler.handle(make_message("shop"))

    assert first.accepted is False
    assert second.accepted is True
    assert service.calls == [
        "select * from orders",
        "SELECT * FROM shop.orders",
    ]


def test_handler_applies_schema_to_all_join_tables_when_one_table_is_ambiguous():
    class PartiallyAmbiguousJoinTaskService:
        def __init__(self):
            self.calls = []

        def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
            self.calls.append(raw_sql)
            if "shop.orders" in raw_sql and "shop.users" in raw_sql:
                return OptimizationTaskExecution(
                    task_id="task-partial-join-ok",
                    status=TaskStatus.COMPLETED,
                    result={
                        "report": OptimizationReport.model_validate(
                            {
                                "task_id": "task-partial-join-ok",
                                "summary": "Use an index.",
                                "confidence": 0.9,
                                "confidence_label": "high",
                                "evidence_status": "full",
                                "missing_evidence": [],
                                "limitations": [],
                                "bottlenecks": [],
                                "sql_rewrites": [],
                                "index_recommendations": [],
                                "risks": [],
                                "validation_steps": [],
                                "similar_cases": [],
                            }
                        )
                    },
                )
            return OptimizationTaskExecution(
                task_id="task-partial-join",
                status=TaskStatus.FAILED,
                error="以下表名在元数据库中存在重复，请补充库名后重试：orders",
            )

    responder = RecordingResponder()
    service = PartiallyAmbiguousJoinTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(
        make_message("SQL优化 select * from orders join users on orders.user_id = users.id")
    )
    second = handler.handle(make_message("shop"))

    assert first.accepted is False
    assert second.accepted is True
    assert "orders, users" in responder.messages[1]
    assert service.calls == [
        "select * from orders join users on orders.user_id = users.id",
        "SELECT * FROM shop.orders JOIN shop.users ON orders.user_id = users.id",
    ]


def test_handler_quotes_hyphenated_schema_name_when_user_replies_with_schema_name():
    responder = RecordingResponder()
    service = AmbiguousTableTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(make_message("SQL优化 select * from orders"))
    second = handler.handle(make_message("international-base"))

    assert first.accepted is False
    assert second.accepted is True
    assert service.calls == [
        "select * from orders",
        "SELECT * FROM `international-base`.orders",
    ]


def test_handler_caches_sql_when_service_stops_for_ambiguous_table():
    responder = RecordingResponder()
    service = FailedAmbiguousTableTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(make_message("SQL优化 select * from orders"))
    second = handler.handle(make_message("shop"))

    assert first.accepted is False
    assert any("请补充数据库库名后继续分析" in text for text in responder.messages)
    assert responder.finished[0] is False
    assert second.accepted is True
    assert service.calls == [
        "select * from orders",
        "SELECT * FROM shop.orders",
    ]


def test_handler_prompts_for_schema_when_route_spans_multiple_instances():
    responder = RecordingResponder()
    service = MultiInstanceRouteTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert "请补充数据库库名后继续分析" in responder.messages[-1]
    assert "SQL 优化任务失败" not in responder.messages[-1]
    assert "# SQL优化报告" not in "".join(responder.messages)
    assert responder.finished[-1] is False


def test_handler_reuses_previous_sql_after_multi_instance_route_prompt():
    responder = RecordingResponder()
    service = MultiInstanceRouteTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(make_message("SQL优化 select * from orders"))
    second = handler.handle(make_message("shop"))

    assert first.accepted is False
    assert second.accepted is True
    assert service.calls == [
        "select * from orders",
        "SELECT * FROM shop.orders",
    ]


def test_handler_prompts_for_schema_when_join_tables_cannot_resolve_database():
    responder = RecordingResponder()
    service = MultiTableSchemaRouteTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(
        make_message("SQL优化 select * from orders join users on orders.user_id = users.id")
    )

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert "请补充数据库库名后继续分析" in responder.messages[-1]
    assert "orders, users" in responder.messages[-1]
    assert "# SQL优化报告" not in "".join(responder.messages)
    assert responder.finished[-1] is False


def test_handler_prompts_for_schema_when_join_table_route_is_missing():
    responder = RecordingResponder()
    service = MultiTableSchemaRouteTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(
        make_message(
            "SQL优化 select count(*) from wmsoutputdetail od "
            "join wmsoutputmain om on od.ChuKuId = om.ChuKuId "
            "left join wmssortingdetail sd on od.yuandanid = sd.sortingId"
        )
    )

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert "请补充数据库库名后继续分析" in responder.messages[-1]
    assert "wmsoutputdetail" in responder.messages[-1]
    assert "wmsoutputmain" in responder.messages[-1]
    assert "wmssortingdetail" in responder.messages[-1]
    assert "# SQL优化报告" not in "".join(responder.messages)


def test_handler_reuses_previous_join_sql_after_schema_prompt():
    responder = RecordingResponder()
    service = MultiTableSchemaRouteTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    first = handler.handle(
        make_message("SQL优化 select * from orders join users on orders.user_id = users.id")
    )
    second = handler.handle(make_message("shop"))

    assert first.accepted is False
    assert second.accepted is True
    assert service.calls == [
        "select * from orders join users on orders.user_id = users.id",
        "SELECT * FROM shop.orders JOIN shop.users ON orders.user_id = users.id",
    ]


def test_fault_handler_runs_diagnosis_and_streams_markdown_report():
    responder = RecordingResponder()
    service = SuccessfulFaultTaskService()
    handler = DingTalkFaultDiagnosisHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("故障诊断 订单系统 CPU 高，IP 10.186.17.54"))

    assert result.accepted is True
    assert result.task_id == "fault-1"
    assert result.status == TaskStatus.COMPLETED
    assert service.calls[0]["input_text"] == "订单系统 CPU 高，IP 10.186.17.54"
    assert responder.messages[0] == FAULT_DIAGNOSIS_STARTED_MESSAGE
    full_stream_text = "".join(responder.messages[1:])
    assert "在解析故障信息..." in responder.messages[0]
    assert "正在获取 TopSQL 和监控指标，请稍候..." in responder.messages[0]
    assert "正在生成故障诊断报告..." in full_stream_text
    assert "正在获取 TopSQL..." not in full_stream_text
    assert "正在获取监控指标..." not in full_stream_text
    assert "### 一、问题简述" in full_stream_text
    assert "### 四、问题分析及优化建议" in full_stream_text


def test_chatdba_handler_routes_fault_prefix_to_fault_handler():
    responder = RecordingResponder()
    sql_service = SuccessfulTaskService()
    fault_service = SuccessfulFaultTaskService()
    handler = DingTalkChatDBAHandler(
        sql_handler=DingTalkSqlOptimizationHandler(
            task_service=sql_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
        fault_handler=DingTalkFaultDiagnosisHandler(
            task_service=fault_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
    )

    result = handler.handle(make_message("故障分析 订单系统 CPU 高，IP 10.186.17.54"))

    assert result.accepted is True
    assert result.task_id == "fault-1"
    assert sql_service.calls == []
    assert fault_service.calls[0]["input_text"] == "订单系统 CPU 高，IP 10.186.17.54"


def test_chatdba_handler_keeps_sql_prefix_on_sql_optimization_handler():
    responder = RecordingResponder()
    sql_service = SuccessfulTaskService()
    fault_service = SuccessfulFaultTaskService()
    handler = DingTalkChatDBAHandler(
        sql_handler=DingTalkSqlOptimizationHandler(
            task_service=sql_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
        fault_handler=DingTalkFaultDiagnosisHandler(
            task_service=fault_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-1"
    assert sql_service.calls[0]["raw_sql"] == "select * from orders"
    assert fault_service.calls == []


def test_chatdba_handler_routes_unprefixed_alert_to_fault_handler():
    responder = RecordingResponder()
    sql_service = SuccessfulTaskService()
    fault_service = SuccessfulFaultTaskService()
    handler = DingTalkChatDBAHandler(
        sql_handler=DingTalkSqlOptimizationHandler(
            task_service=sql_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
        fault_handler=DingTalkFaultDiagnosisHandler(
            task_service=fault_service,
            responder=responder,
            stream_interval_ms=1000,
        ),
    )
    alert = (
        "【系统:ZJ_生产数据库维护】2026-05-13 09:45:03 "
        "实例：10.187.0.54|mysql_server_8801，ip：10.187.0.54，"
        "指标名称：<数据库主进程是否存在> 发生异常"
    )

    result = handler.handle(make_message(alert))

    assert result.accepted is True
    assert result.task_id == "fault-1"
    assert sql_service.calls == []
    assert fault_service.calls[0]["input_text"] == alert
