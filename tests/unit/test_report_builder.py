from chatdba.cases.repository import OptimizationCase
from chatdba.domain.models import (
    ConfidenceLabel,
    EvidenceEnvelope,
    EvidenceStatus,
    RuleFinding,
    SqlFeatures,
)
from chatdba.workflow.report_builder import OptimizationReportComposer


def test_report_builder_creates_sql_only_report_without_qwen():
    composer = OptimizationReportComposer(cases=[])

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from orders order by created_at desc limit 20",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            order_by=["created_at DESC"],
            has_limit=True,
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=["No metadata route found for one or more tables."],
        ),
        findings=[
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="ORDER BY with LIMIT may require a supporting index.",
                evidence={"order_by": ["created_at DESC"]},
            )
        ],
    )

    assert report.task_id == "task-1"
    assert report.evidence_status == EvidenceStatus.SQL_ONLY
    assert report.confidence_label == ConfidenceLabel.LOW
    assert "No source execution evidence was available." in report.limitations[0]


def test_report_builder_uses_cases_and_qwen_json_when_available():
    class FakeQwenGateway:
        def generate_report(self, system_prompt: str, user_prompt: str) -> str:
            assert "SQL优化报告" in system_prompt
            assert "filesort fixed" in user_prompt
            return """
            {
              "task_id": "task-1",
              "summary": "Use an index to avoid filesort.",
              "confidence": 0.78,
              "confidence_label": "medium",
              "evidence_status": "partial",
              "missing_evidence": ["create_table"],
              "limitations": ["DDL could not be collected."],
              "bottlenecks": [{"code": "full_table_scan", "evidence": "rows examined is high"}],
              "sql_rewrites": [{"title": "Rewrite", "sql": "select * from orders"}],
              "index_recommendations": [{"ddl": "create index idx_orders_created_at on orders(created_at)", "risk": "medium"}],
              "risks": [{"level": "medium", "description": "Review online DDL strategy."}],
              "validation_steps": ["Run EXPLAIN FORMAT=JSON again after creating the index."],
              "similar_cases": [{"case_id": "case-1", "reason": "same filesort symptom"}]
            }
            """

    composer = OptimizationReportComposer(
        qwen_gateway=FakeQwenGateway(),
        cases=[
            OptimizationCase(
                case_id="case-1",
                db_type="mysql",
                scenario_tags=["order_by"],
                case_card="filesort fixed",
                quality_score=0.9,
            )
        ],
    )

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from orders order by created_at desc limit 20",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            order_by=["created_at DESC"],
            has_limit=True,
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.PARTIAL,
            missing_evidence=["create_table"],
            collection_errors=["Failed to collect table DDL: timeout"],
        ),
        findings=[],
    )

    assert report.summary == "Use an index to avoid filesort."
    assert report.similar_cases[0].case_id == "case-1"
