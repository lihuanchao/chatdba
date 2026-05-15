from datetime import datetime

from chatdba.domain.fault_diagnosis import (
    MetricEvidence,
    MetricPoint,
    MetricSeries,
    TopSqlEvidence,
    TopSqlRecord,
)
from chatdba.workflow.fault_diagnosis import build_fault_diagnosis_graph


class FakeTopSqlAgent:
    def __init__(self) -> None:
        self.seen_profile = None

    def analyze(self, profile):
        self.seen_profile = profile
        return TopSqlEvidence(
            status="success",
            rows=[
                TopSqlRecord(
                    database="orders",
                    execution_count=12,
                    avg_execution_seconds=3.42,
                    total_execution_seconds=41.04,
                    sql_text="select * from orders order by created_at desc limit 1000",
                )
            ],
            summary="发现 1 条长时间运行 TopSQL，最长运行 38 秒。",
        )


class MultiTopSqlAgent:
    def analyze(self, profile):
        return TopSqlEvidence(
            status="success",
            rows=[
                TopSqlRecord(
                    database="orders",
                    execution_count=120,
                    avg_execution_seconds=5.2,
                    total_execution_seconds=624.0,
                    sql_text=(
                        "select * from orders where status = ? "
                        "order by created_at desc limit ?"
                    ),
                ),
                TopSqlRecord(
                    database="orders",
                    execution_count=88,
                    avg_execution_seconds=3.7,
                    total_execution_seconds=325.6,
                    sql_text="select count(*) from order_items where order_id = ?",
                ),
                TopSqlRecord(
                    database="orders",
                    execution_count=50,
                    avg_execution_seconds=2.9,
                    total_execution_seconds=145.0,
                    sql_text="select * from audit_log where tenant_id = ?",
                ),
                TopSqlRecord(
                    database="orders",
                    execution_count=42,
                    avg_execution_seconds=2.1,
                    total_execution_seconds=88.2,
                    sql_text="select * from shipments where status = ?",
                ),
                TopSqlRecord(
                    database="orders",
                    execution_count=36,
                    avg_execution_seconds=1.8,
                    total_execution_seconds=64.8,
                    sql_text="select * from payments where created_at > ?",
                ),
                TopSqlRecord(
                    database="orders",
                    execution_count=30,
                    avg_execution_seconds=1.5,
                    total_execution_seconds=45.0,
                    sql_text="select * from inventory where sku = ?",
                ),
            ],
            summary="发现多条疑似相关 TopSQL。",
        )


class BacktickTopSqlAgent:
    def analyze(self, profile):
        return TopSqlEvidence(
            status="success",
            rows=[
                TopSqlRecord(
                    database="international-base",
                    execution_count=10,
                    avg_execution_seconds=2.5,
                    total_execution_seconds=25.0,
                    sql_text=(
                        "SELECT FILE_UPLOAD_INFO_ID FROM "
                        "`international-base`.sys_file_info WHERE BILL_ID = ?"
                    ),
                )
            ],
            summary="发现 1 条疑似相关 TopSQL。",
        )


class FakeMetricAgent:
    def __init__(self) -> None:
        self.seen_profile = None

    def analyze(self, profile):
        self.seen_profile = profile
        return MetricEvidence(
            status="success",
            metrics=[
                MetricSeries(
                    metric_name="cpu_usage",
                    ip=profile.business_ip or "",
                    unit="%",
                    values=[
                        MetricPoint(timestamp=1777527000, value=91.2),
                        MetricPoint(timestamp=1777527060, value=93.5),
                    ],
                )
            ],
            summary="CPU 使用率持续高于 90%。",
        )


class PartialMetricAgent:
    def analyze(self, profile):
        return MetricEvidence(
            status="success",
            metrics=[
                MetricSeries(
                    metric_name="cpu_usage",
                    ip=profile.business_ip or "",
                    unit="%",
                    values=[MetricPoint(timestamp=1777527000, value=91.2)],
                )
            ],
            summary="CPU 使用率峰值为 91.2%。",
            missing_metrics=["active_threads: 未返回数据"],
            error_message="active_threads: 未返回数据",
        )


class FailingTopSqlAgent:
    def analyze(self, profile):
        return TopSqlEvidence(
            status="failure",
            rows=[],
            error_message="performance_schema 连接超时",
        )


class SparseReportGateway:
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            raise RuntimeError("use fallback profile")
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常，但 TopSQL 证据缺失。"
        return "### 模型报告\n已分析。"


class SparseTopSqlReportGateway:
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            raise RuntimeError("use fallback profile")
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常与 TopSQL 证据同时存在，疑似 TopSQL 导致数据库高负载。"
        return "### 模型报告\n已分析。"


class AppendixReportGateway:
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            raise RuntimeError("use fallback profile")
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常与 TopSQL 证据同时存在，疑似 TopSQL 导致数据库高负载。"
        return (
            "### 一、问题简述\n"
            "数据库 CPU 异常。\n\n"
            "### 四、问题分析\n"
            "TopSQL 分析：存在高耗时 SQL。\n\n"
            "五、附录：数据来源说明\n"
            "监控指标：来自业务 IP 10.186.21.61 的 CPU 使用率。\n"
            "TopSQL：基于数据库性能采集系统。\n"
            "CMDB 映射：通过 CMDB 将管理 IP 映射至业务 IP。\n"
        )


class MarkdownNumberedAppendixReportGateway:
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            raise RuntimeError("use fallback profile")
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常与 TopSQL 证据同时存在，疑似 TopSQL 导致数据库高负载。"
        return (
            "### 一、问题简述\n"
            "数据库 CPU 异常。\n\n"
            "### 四、问题分析\n"
            "TopSQL 分析：存在高耗时 SQL。\n\n"
            "### 六、附录：关键数据摘要\n"
            "| 监控指标峰值 | TopSQL（前5条） |\n"
            "| --- | --- |\n"
            "| CPU 95% | select * from orders |\n"
        )


class CapturingReportPromptGateway:
    def __init__(self) -> None:
        self.report_system_prompt = ""

    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            raise RuntimeError("use fallback profile")
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常与 TopSQL 证据同时存在，疑似 TopSQL 导致数据库高负载。"
        self.report_system_prompt = system_prompt
        return "### 一、问题简述\n数据库 CPU 异常。"


class RecordingFaultGateway:
    def __init__(self) -> None:
        self.operations: list[str] = []

    def usage_operation(self, operation: str):
        gateway = self

        class Context:
            def __enter__(self):
                gateway.operations.append(operation)

            def __exit__(self, exc_type, exc, tb):
                return False

        return Context()

    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if "FaultDiagnosisProfile" in system_prompt:
            return """
            {
              "input_text": "订单系统 CPU 高",
              "system_name": "订单系统",
              "management_ip": "10.186.17.54",
              "business_ip": "10.186.17.55",
              "primary_ip": "10.186.17.54",
              "alert_time": null,
              "start_time": "2026-04-30 14:00:00",
              "end_time": "2026-04-30 15:00:00",
              "timezone": "Asia/Shanghai",
              "query_background": "订单系统数据库故障诊断",
              "plan": [],
              "missing_fields": []
            }
            """
        if "根因仲裁结论" in system_prompt:
            return "监控指标异常与 TopSQL 证据同时存在。"
        return "### 一、问题简述\n数据库 CPU 异常。"


class FakeCmdbResolver:
    def resolve_by_management_ip(self, management_ip: str):
        if management_ip == "10.187.0.54":
            return {
                "management_ip": management_ip,
                "business_ip": "10.187.0.55",
                "system_name": "ZJ_生产数据库维护",
            }
        assert management_ip == "10.186.17.54"
        return {
            "management_ip": management_ip,
            "business_ip": "10.186.17.55",
            "system_name": "订单系统",
        }


def test_fault_diagnosis_graph_collects_top_sql_metrics_and_builds_markdown_report():
    top_sql_agent = FakeTopSqlAgent()
    metric_agent = FakeMetricAgent()
    graph = build_fault_diagnosis_graph(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-1",
            "input_text": (
                "请分析如下系统数据库 CPU 告警。系统名称：订单系统，"
                "管理IP：10.186.17.54，时间：最近1小时"
            ),
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    profile = result["profile"]
    report = result["report"]

    assert profile.system_name == "订单系统"
    assert profile.management_ip == "10.186.17.54"
    assert profile.business_ip == "10.186.17.55"
    assert profile.primary_ip == "10.186.17.54"
    assert profile.start_time == "2026-04-30 14:00:00"
    assert profile.end_time == "2026-04-30 15:00:00"
    assert top_sql_agent.seen_profile is profile
    assert metric_agent.seen_profile is profile
    assert report.task_id == "fault-1"
    assert "### 一、问题简述" in report.markdown
    assert "【报告生成时间】2026-04-30 15:00:00" in report.markdown
    assert "订单系统" in report.markdown
    assert "10.186.17.54" in report.markdown
    assert "10.186.17.55" in report.markdown


def test_fault_diagnosis_graph_labels_qwen_usage_operations():
    gateway = RecordingFaultGateway()
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=gateway,
    )

    graph.invoke(
        {
            "task_id": "fault-usage",
            "input_text": "订单系统 CPU 高，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    assert gateway.operations == [
        "fault_profile",
        "fault_adjudication",
        "fault_report",
    ]


def test_fault_report_lists_two_top_sql_rows_and_separates_analysis_from_recommendations():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=MultiTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-top-sql-limit",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    markdown = result["report"].markdown

    assert "【相关SQL及初步优化建议】" not in markdown
    assert "### 相关 SQL 及初步优化建议" not in markdown
    assert "### 四、问题分析" in markdown
    assert "### 五、优化建议" in markdown
    assert "select * from orders where status" in markdown
    assert "select count(*) from order_items" in markdown
    assert "select * from audit_log" not in markdown
    assert "select * from shipments" not in markdown
    assert "select * from payments" not in markdown
    assert "select * from inventory" not in markdown
    assert "TopSQL 分析：共获取 6 条，展示前 2 条" in markdown
    assert "执行 EXPLAIN 确认扫描行数和 filesort" in markdown
    assert "执行 EXPLAIN 确认扫描行数；优先评估统计条件字段的覆盖索引" in markdown


def test_fault_report_formats_sql_with_backticks_as_markdown_code():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=BacktickTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-sql-code",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    markdown = result["report"].markdown

    assert (
        "``SELECT FILE_UPLOAD_INFO_ID FROM `international-base`.sys_file_info "
        "WHERE BILL_ID = ?``"
    ) in markdown
    assert (
        "```sql\nSELECT FILE_UPLOAD_INFO_ID FROM "
        "`international-base`.sys_file_info WHERE BILL_ID = ?\n```"
    ) not in markdown
    assert "SQL：`SELECT FILE_UPLOAD_INFO_ID FROM `international-base`" not in markdown


def test_fault_diagnosis_uses_30_minute_window_before_alert_time():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-2",
            "input_text": (
                "请分析订单系统数据库 CPU 告警，管理IP：10.186.17.54，"
                "告警时间：2026-04-30 14:20:00"
            ),
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    profile = result["profile"]

    assert profile.start_time == "2026-04-30 13:50:00"
    assert profile.end_time == "2026-04-30 14:20:00"
    assert profile.alert_time == "2026-04-30 14:20:00"


def test_fault_diagnosis_extracts_time_and_management_ip_from_real_alert_text():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-3",
            "input_text": (
                "【系统:告警文本系统名不可信】2026-05-13 09:45:03 "
                "实例：10.187.0.54|mysql_server_8801，ip：10.187.0.54，"
                "指标名称：<数据库主进程是否存在> 发生异常，当前指标值：'0'，"
                "请及时关注【同心云】"
            ),
            "current_time": datetime(2026, 5, 13, 10, 0, 0),
        }
    )

    profile = result["profile"]

    assert profile.system_name == "ZJ_生产数据库维护"
    assert profile.management_ip == "10.187.0.54"
    assert profile.primary_ip == "10.187.0.54"
    assert profile.business_ip == "10.187.0.55"
    assert profile.alert_time == "2026-05-13 09:45:03"
    assert profile.start_time == "2026-05-13 09:15:03"
    assert profile.end_time == "2026-05-13 09:45:03"


def test_fault_report_shows_partial_metric_missing_details():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=PartialMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-4",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "部分指标缺失" in report.summary
    assert "active_threads: 未返回数据" in report.markdown
    assert "未获取到的监控指标" in report.markdown


def test_fault_report_shows_top_sql_failure_reason():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FailingTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-5",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "TopSQL 获取失败" in report.summary
    assert "performance_schema 连接超时" in report.markdown
    assert "未获取到有效 TopSQL" in report.markdown


def test_fault_report_appends_missing_evidence_when_model_report_omits_it():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FailingTopSqlAgent(),
        metric_agent=PartialMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=SparseReportGateway(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-6",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "### 模型报告" in report.markdown
    assert "### 证据采集缺口" in report.markdown
    assert "active_threads: 未返回数据" in report.markdown
    assert "performance_schema 连接超时" in report.markdown


def test_fault_report_appends_related_top_sql_when_model_report_omits_it():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=SparseTopSqlReportGateway(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-7",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "### 模型报告" in report.markdown
    assert "【报告生成时间】2026-04-30 15:00:00" in report.markdown
    assert "### 相关 SQL 及初步优化建议" not in report.markdown
    assert "附录：关键数据摘要" not in report.markdown


def test_fault_report_strips_plain_text_appendix_data_source_section_from_model_report():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=AppendixReportGateway(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-8",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "### 一、问题简述" in report.markdown
    assert "### 四、问题分析" in report.markdown
    assert "【报告生成时间】2026-04-30 15:00:00" in report.markdown
    assert "附录" not in report.markdown
    assert "数据来源说明" not in report.markdown
    assert "监控指标：来自业务 IP" not in report.markdown
    assert "CMDB 映射" not in report.markdown


def test_fault_report_strips_markdown_numbered_appendix_summary_section_from_model_report():
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=MarkdownNumberedAppendixReportGateway(),
    )

    result = graph.invoke(
        {
            "task_id": "fault-9",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    report = result["report"]

    assert "### 一、问题简述" in report.markdown
    assert "### 四、问题分析" in report.markdown
    assert "【报告生成时间】2026-04-30 15:00:00" in report.markdown
    assert "附录" not in report.markdown
    assert "关键数据摘要" not in report.markdown
    assert "监控指标峰值" not in report.markdown
    assert "TopSQL（前5条）" not in report.markdown


def test_fault_report_prompt_restricts_model_to_fixed_sections_and_bans_appendix():
    gateway = CapturingReportPromptGateway()
    graph = build_fault_diagnosis_graph(
        top_sql_agent=FakeTopSqlAgent(),
        metric_agent=FakeMetricAgent(),
        cmdb_resolver=FakeCmdbResolver(),
        qwen_gateway=gateway,
    )

    graph.invoke(
        {
            "task_id": "fault-10",
            "input_text": "订单系统数据库 CPU 告警，管理IP：10.186.17.54",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        }
    )

    prompt = gateway.report_system_prompt

    assert "只能输出以下五个一级章节" in prompt
    assert "### 一、问题简述" in prompt
    assert "### 二、影响概述" in prompt
    assert "### 三、问题原因" in prompt
    assert "### 四、问题分析" in prompt
    assert "### 五、优化建议" in prompt
    assert "禁止输出任何附录" in prompt
    assert "数据来源说明" in prompt
    assert "关键数据摘要" in prompt
    assert "相关 SQL 及初步优化建议" in prompt
