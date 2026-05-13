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
    assert "订单系统" in report.markdown
    assert "10.186.17.54" in report.markdown
    assert "10.186.17.55" in report.markdown
    assert "select * from orders" in report.markdown
    assert "CPU 使用率持续高于 90%" in report.markdown


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
