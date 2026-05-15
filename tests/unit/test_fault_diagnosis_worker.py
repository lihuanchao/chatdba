from datetime import datetime

from chatdba.domain.fault_diagnosis import TopSqlEvidence
from chatdba.worker.run_fault_diagnosis import run_fault_diagnosis_task


class EmptyTopSqlAgent:
    def analyze(self, profile):
        return TopSqlEvidence(status="failure", rows=[], error_message="top sql unavailable")


class EmptyMetricAgent:
    def analyze(self, profile):
        from chatdba.domain.fault_diagnosis import MetricEvidence

        return MetricEvidence(
            status="failure",
            metrics=[],
            error_message="metrics unavailable",
        )


def test_run_fault_diagnosis_task_emits_progress_and_returns_report():
    progress = []

    result = run_fault_diagnosis_task(
        {
            "task_id": "fault-2",
            "input_text": "数据库告警，IP 10.1.2.3，最近1小时 CPU 高",
            "current_time": datetime(2026, 4, 30, 15, 0, 0),
        },
        top_sql_agent=EmptyTopSqlAgent(),
        metric_agent=EmptyMetricAgent(),
        progress_sink=progress.append,
    )

    assert progress == [
        "正在生成故障诊断报告...\n",
    ]
    assert result["report"].task_id == "fault-2"
    assert "证据不足" in result["report"].markdown
