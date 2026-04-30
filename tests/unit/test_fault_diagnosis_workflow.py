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
                    running_seconds=38,
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
                    ip="10.186.17.54",
                    unit="%",
                    values=[
                        MetricPoint(timestamp=1777527000, value=91.2),
                        MetricPoint(timestamp=1777527060, value=93.5),
                    ],
                )
            ],
            summary="CPU 使用率持续高于 90%。",
        )


def test_fault_diagnosis_graph_collects_top_sql_metrics_and_builds_markdown_report():
    top_sql_agent = FakeTopSqlAgent()
    metric_agent = FakeMetricAgent()
    graph = build_fault_diagnosis_graph(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
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
    assert profile.primary_ip == "10.186.17.54"
    assert profile.start_time == "2026-04-30 14:00:00"
    assert profile.end_time == "2026-04-30 15:00:00"
    assert top_sql_agent.seen_profile is profile
    assert metric_agent.seen_profile is profile
    assert report.task_id == "fault-1"
    assert "### 一、问题简述" in report.markdown
    assert "订单系统" in report.markdown
    assert "10.186.17.54" in report.markdown
    assert "select * from orders" in report.markdown
    assert "CPU 使用率持续高于 90%" in report.markdown
