from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus
from chatdba.domain.report_schema import OptimizationReport, Risk


def test_render_report_for_dingtalk_hides_task_id_risks_and_validation_steps():
    report = OptimizationReport(
        task_id="task-1",
        summary="Use an index.",
        confidence=0.9,
        confidence_label=ConfidenceLabel.HIGH,
        evidence_status=EvidenceStatus.FULL,
        missing_evidence=[],
        limitations=[],
        bottlenecks=[],
        sql_rewrites=[],
        index_recommendations=[],
        risks=[Risk(level="medium", description="Review online DDL strategy.")],
        validation_steps=["Run EXPLAIN FORMAT=JSON."],
        similar_cases=[],
    )

    markdown = render_report_for_dingtalk(report)

    assert "任务ID" not in markdown
    assert "task-1" not in markdown
    assert "风险提示" not in markdown
    assert "Review online DDL strategy." not in markdown
    assert "验证步骤" not in markdown
    assert "Run EXPLAIN FORMAT=JSON." not in markdown
    assert "证据级别" in markdown
    assert "置信度" in markdown
