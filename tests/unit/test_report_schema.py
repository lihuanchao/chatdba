import pytest
from pydantic import ValidationError

from chatdba.domain.models import ConfidenceLabel, EvidenceStatus
from chatdba.domain.report_schema import OptimizationReport


def test_report_accepts_required_sections():
    report = OptimizationReport.model_validate(
        {
            "task_id": "task-1",
            "summary": "Full table scan on orders.",
            "confidence": 0.82,
            "confidence_label": "high",
            "evidence_status": "full",
            "missing_evidence": [],
            "limitations": [],
            "bottlenecks": [{"code": "full_table_scan", "evidence": "rows_examined is high"}],
            "sql_rewrites": [{"title": "Use sargable predicate", "sql": "select * from orders where created_at >= ?"}],
            "index_recommendations": [{"ddl": "create index idx_orders_created_at on orders(created_at)", "risk": "medium"}],
            "risks": [{"level": "medium", "description": "Index build needs online DDL review"}],
            "validation_steps": ["Run EXPLAIN FORMAT=JSON on the rewritten SQL"],
            "similar_cases": [{"case_id": "case-1", "reason": "same filesort symptom"}],
        }
    )

    assert report.task_id == "task-1"
    assert report.confidence == 0.82
    assert report.confidence_label == ConfidenceLabel.HIGH
    assert report.evidence_status == EvidenceStatus.FULL


def test_report_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        OptimizationReport.model_validate(
            {
                "task_id": "task-1",
                "summary": "Invalid confidence.",
                "confidence": 1.5,
                "confidence_label": "low",
                "evidence_status": "sql_only",
                "missing_evidence": ["route_info"],
                "limitations": ["No source execution evidence was available."],
                "bottlenecks": [],
                "sql_rewrites": [],
                "index_recommendations": [],
                "risks": [],
                "validation_steps": [],
                "similar_cases": [],
            }
        )
