from chatdba.cases.repository import OptimizationCase
from chatdba.domain.models import (
    ConfidenceLabel,
    EvidenceEnvelope,
    EvidenceStatus,
    RuleFinding,
    SourceRoute,
    SqlFeatures,
)
from chatdba.workflow.report_builder import OptimizationReportComposer, _load_system_prompt


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
    assert "未获取到源库执行证据" in report.limitations[0]


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


def test_report_builder_selects_cases_by_environment_plan_and_root_cause():
    composer = OptimizationReportComposer(
        cases=[
            OptimizationCase(
                case_id="generic-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by"],
                case_card="generic order by case",
                quality_score=1.0,
            ),
            OptimizationCase(
                case_id="exact-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                plan_symptom_tags=["using_filesort"],
                root_cause_tags=["missing_composite_index"],
                case_card="exact filesort missing composite index case",
                quality_score=0.2,
            ),
            OptimizationCase(
                case_id="wrong-version",
                db_type="mysql",
                db_version_major="5.7",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                plan_symptom_tags=["using_filesort"],
                root_cause_tags=["missing_composite_index"],
                case_card="wrong version case",
                quality_score=1.0,
            ),
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
            route=SourceRoute(
                instance_id="mysql-1",
                db_type="mysql",
                version="8.0.36",
            ),
            explain_json={
                "query_block": {
                    "ordering_operation": {
                        "using_filesort": True,
                    }
                }
            },
        ),
        findings=[
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="ORDER BY with LIMIT may require a supporting index.",
            )
        ],
    )

    assert [case.case_id for case in report.similar_cases] == [
        "exact-case",
        "generic-case",
    ]


def test_report_builder_loads_markdown_system_prompt_file():
    prompt = _load_system_prompt()

    assert "# ChatDBA SQL优化报告生成提示词（中文）" in prompt
    assert "仅返回合法 JSON" in prompt
