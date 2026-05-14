from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import CaseRetrievalQuery
from chatdba.domain.models import (
    ConfidenceLabel,
    EvidenceEnvelope,
    EvidenceStatus,
    RuleFinding,
    SourceRoute,
    SqlFeatures,
    TableReference,
)
from chatdba.workflow.report_builder import OptimizationReportComposer, _load_system_prompt


EMPTY_PROBLEM_PROFILE_JSON = """
{
  "scenario_tags": [],
  "plan_symptom_tags": [],
  "root_cause_tags": [],
  "problem_summary": "",
  "confidence": "low",
  "evidence": []
}
"""


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
            if "SQL问题画像" in system_prompt:
                return EMPTY_PROBLEM_PROFILE_JSON
            assert "SQL优化报告" in system_prompt
            assert "summary`、`sql_rewrites`、`index_recommendations` 必须综合" in system_prompt
            assert "`summary` 必须是 30 字以内的精简自然语言结论" in system_prompt
            assert "`summary` 禁止输出命中规则列表" in system_prompt
            assert "SQL重写建议（`sql_rewrites`）必须基于表结构、执行计划、关联案例综合分析" in system_prompt
            assert "索引推荐（`index_recommendations`）必须先检查现有 DDL" in system_prompt
            assert "`id`、`select_type`、`table`、`type`、`possible_keys`、`key`、`key_len`、`ref`、`Extra`" in system_prompt
            assert "`SELECT` 字段与索引覆盖" in system_prompt
            assert "若 DDL 中已存在相同索引或可覆盖该建议的联合索引，禁止重复推荐" in system_prompt
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


def test_report_builder_backfills_retrieved_cases_when_qwen_omits_them():
    class FakeQwenGateway:
        def generate_report(self, system_prompt: str, user_prompt: str) -> str:
            if "SQL问题画像" in system_prompt:
                return EMPTY_PROBLEM_PROFILE_JSON
            assert "exact filesort case" in user_prompt
            return """
            {
              "task_id": "task-1",
              "summary": "Use a composite index to avoid filesort.",
              "confidence": 0.78,
              "confidence_label": "medium",
              "evidence_status": "partial",
              "missing_evidence": [],
              "limitations": [],
              "bottlenecks": [{"code": "limit_with_order_by", "evidence": "Using filesort"}],
              "sql_rewrites": [{"title": "Rewrite", "sql": "select * from orders"}],
              "index_recommendations": [{"ddl": "create index idx_orders_created_at on orders(created_at)", "risk": "medium"}],
              "risks": [],
              "validation_steps": ["Run EXPLAIN FORMAT=JSON again."],
              "similar_cases": []
            }
            """

    composer = OptimizationReportComposer(
        qwen_gateway=FakeQwenGateway(),
        cases=[
            OptimizationCase(
                case_id="case-filesort-1",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                plan_symptom_tags=["using_filesort"],
                root_cause_tags=["missing_composite_index"],
                case_card="exact filesort case",
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
            route=SourceRoute(
                instance_id="mysql-1",
                db_type="mysql",
                version="8.0.36",
            ),
            explain_json={"query_block": {"ordering_operation": {"using_filesort": True}}},
        ),
        findings=[
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="ORDER BY with LIMIT may require a supporting index.",
            )
        ],
    )

    assert [case.case_id for case in report.similar_cases] == ["case-filesort-1"]
    assert "根因标签命中：missing_composite_index" in report.similar_cases[0].reason
    assert "执行计划症状命中：using_filesort" in report.similar_cases[0].reason
    assert "SQL场景命中：order_by, limit" in report.similar_cases[0].reason
    assert composer.last_case_retrieval_debug == {
        "query": {
            "db_type": "mysql",
            "db_version_major": "8.0",
            "sql_type": "select",
            "scenario_tags": ["order_by", "limit"],
            "plan_symptom_tags": ["using_filesort"],
            "root_cause_tags": ["missing_composite_index"],
        },
        "matched_cases": [
            {
                "case_id": "case-filesort-1",
                "reason": report.similar_cases[0].reason,
            }
        ],
    }


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
            tables=[
                TableReference(schema_name="shop", table_name="orders", alias="o"),
                TableReference(schema_name="shop", table_name="users", alias="u"),
            ],
            predicates=["WHERE o.tenant_id = 10001 AND o.status = 1"],
            joins=["JOIN shop.users AS u ON u.id = o.user_id"],
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
    assert report.index_recommendations[0].ddl == (
        "CREATE INDEX idx_orders_tenant_id_status_created_at_user_id "
        "ON orders(tenant_id, status, created_at, user_id);"
    )


def test_report_builder_fallback_summary_references_plan_or_ddl_evidence():
    composer = OptimizationReportComposer(
        cases=[
            OptimizationCase(
                case_id="case-filesort-1",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                plan_symptom_tags=["using_filesort"],
                root_cause_tags=["missing_composite_index"],
                case_card="filesort case",
                quality_score=0.9,
            )
        ]
    )

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from orders order by created_at desc limit 20",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(schema_name="shop", table_name="orders")],
            order_by=["created_at DESC"],
            has_limit=True,
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            route=SourceRoute(
                instance_id="mysql-1",
                db_type="mysql",
                version="8.0.36",
            ),
            explain_json={"query_block": {"ordering_operation": {"using_filesort": True}}},
            create_tables={
                "shop.orders": (
                    "CREATE TABLE orders (tenant_id bigint, status tinyint, "
                    "created_at datetime, user_id bigint)"
                )
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

    assert "filesort" in report.summary.lower() or "执行计划" in report.summary


def test_report_builder_loads_markdown_system_prompt_file():
    prompt = _load_system_prompt()

    assert "# ChatDBA SQL优化报告生成提示词（中文）" in prompt
    assert "仅返回合法 JSON" in prompt
    assert "summary`、`sql_rewrites`、`index_recommendations` 必须综合" in prompt
    assert "`summary` 必须是 30 字以内的精简自然语言结论" in prompt
    assert "`summary` 禁止输出命中规则列表" in prompt
    assert "SQL重写建议（`sql_rewrites`）必须基于表结构、执行计划、关联案例综合分析" in prompt
    assert "索引推荐（`index_recommendations`）必须先检查现有 DDL" in prompt
    assert "`id`、`select_type`、`table`、`type`、`possible_keys`、`key`、`key_len`、`ref`、`Extra`" in prompt
    assert "`SELECT` 字段与索引覆盖" in prompt
    assert "若 DDL 中已存在相同索引或可覆盖该建议的联合索引，禁止重复推荐" in prompt


def test_report_builder_uses_case_retriever_when_available():
    class RecordingCaseRetriever:
        def __init__(self):
            self.query = None
            self.limit = None

        def retrieve(self, query: CaseRetrievalQuery, *, limit: int):
            self.query = query
            self.limit = limit
            return [
                OptimizationCase(
                    case_id="hybrid-case",
                    db_type="mysql",
                    db_version_major="8.0",
                    sql_type="select",
                    scenario_tags=["order_by", "limit"],
                    plan_symptom_tags=["using_filesort"],
                    root_cause_tags=["missing_composite_index"],
                    case_card="hybrid retrieval case",
                )
            ]

    case_retriever = RecordingCaseRetriever()
    composer = OptimizationReportComposer(
        cases=[],
        case_retriever=case_retriever,
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
            explain_json={"query_block": {"ordering_operation": {"using_filesort": True}}},
        ),
        findings=[
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="ORDER BY with LIMIT may require a supporting index.",
            )
        ],
    )

    assert [case.case_id for case in report.similar_cases] == ["hybrid-case"]
    assert case_retriever.query is not None
    assert case_retriever.query.db_type == "mysql"
    assert case_retriever.query.db_version_major == "8.0"
    assert case_retriever.query.sql_type == "select"
    assert case_retriever.query.plan_symptom_tags == ["using_filesort"]
    assert case_retriever.limit == 3


def test_report_builder_uses_qwen_problem_profile_for_case_retrieval():
    class FakeQwenGateway:
        def generate_report(self, system_prompt: str, user_prompt: str) -> str:
            if "SQL问题画像" in system_prompt:
                assert "user_name = 123" in user_prompt
                return """
                {
                  "scenario_tags": ["where_filter", "equality_predicate"],
                  "plan_symptom_tags": ["index_not_used"],
                  "root_cause_tags": ["implicit_cast"],
                  "problem_summary": "字符串列使用数字字面量比较，可能触发隐式类型转换。",
                  "confidence": "high",
                  "evidence": ["users.user_name 为 varchar", "谓词为 user_name = 123"]
                }
                """
            assert '"problem_profile"' in user_prompt
            assert "implicit cast case" in user_prompt
            return """
            {
              "task_id": "task-1",
              "summary": "修正参数类型，避免隐式转换。",
              "confidence": 0.88,
              "confidence_label": "high",
              "evidence_status": "full",
              "missing_evidence": [],
              "limitations": [],
              "bottlenecks": [{"code": "implicit_cast", "evidence": "user_name 是字符串列但传入数字字面量"}],
              "sql_rewrites": [{"title": "修正参数类型", "sql": "select * from users where user_name = '123'"}],
              "index_recommendations": [],
              "risks": [],
              "validation_steps": ["重新执行 EXPLAIN FORMAT=JSON，确认命中 user_name 索引。"],
              "similar_cases": []
            }
            """

    class RecordingCaseRetriever:
        def __init__(self):
            self.query = None

        def retrieve(self, query: CaseRetrievalQuery, *, limit: int):
            self.query = query
            if "implicit_cast" not in query.root_cause_tags:
                return []
            return [
                OptimizationCase(
                    case_id="implicit-case",
                    db_type="mysql",
                    sql_type="select",
                    scenario_tags=["where_filter", "equality_predicate"],
                    plan_symptom_tags=["index_not_used"],
                    root_cause_tags=["implicit_cast"],
                    case_card="implicit cast case",
                )
            ]

    case_retriever = RecordingCaseRetriever()
    composer = OptimizationReportComposer(
        cases=[],
        qwen_gateway=FakeQwenGateway(),
        case_retriever=case_retriever,
    )

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from users where user_name = 123;",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(table_name="users")],
            predicates=["user_name = 123"],
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            create_tables={
                "shop.users": "CREATE TABLE `users` (`user_name` varchar(64) NOT NULL)"
            },
        ),
        findings=[],
    )

    assert case_retriever.query is not None
    assert case_retriever.query.scenario_tags == ["where_filter", "equality_predicate"]
    assert case_retriever.query.plan_symptom_tags == ["index_not_used"]
    assert case_retriever.query.root_cause_tags == ["implicit_cast"]
    assert [case.case_id for case in report.similar_cases] == ["implicit-case"]


def test_report_builder_keeps_rewrite_profile_tags_for_case_retrieval():
    class FakeQwenGateway:
        def generate_report(self, system_prompt: str, user_prompt: str) -> str:
            if "SQL问题画像" in system_prompt:
                return """
                {
                  "scenario_tags": ["subquery", "aggregate", "having"],
                  "plan_symptom_tags": ["dependent_subquery", "using_temporary"],
                  "root_cause_tags": ["count_subquery_to_exists", "having_not_pushed_down"],
                  "problem_summary": "COUNT 标量子查询和 HAVING 过滤可以改写。",
                  "confidence": "medium",
                  "evidence": ["存在 count(*) > 0 子查询", "HAVING 条件不含聚合函数"]
                }
                """
            return """
            {
              "task_id": "task-1",
              "summary": "Use rewrite cases.",
              "confidence": 0.7,
              "confidence_label": "medium",
              "evidence_status": "sql_only",
              "missing_evidence": [],
              "limitations": [],
              "bottlenecks": [{"code": "rewrite", "evidence": "profile tags"}],
              "sql_rewrites": [{"title": "Rewrite", "sql": "select * from customer"}],
              "index_recommendations": [],
              "risks": [],
              "validation_steps": ["Run EXPLAIN FORMAT=JSON."],
              "similar_cases": []
            }
            """

    class RecordingCaseRetriever:
        def __init__(self):
            self.query = None

        def retrieve(self, query: CaseRetrievalQuery, *, limit: int):
            self.query = query
            return []

    case_retriever = RecordingCaseRetriever()
    composer = OptimizationReportComposer(
        cases=[],
        qwen_gateway=FakeQwenGateway(),
        case_retriever=case_retriever,
    )

    composer.compose(
        task_id="task-1",
        raw_sql=(
            "select * from customer where "
            "(select count(*) from orders where c_custkey=o_custkey) > 0"
        ),
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(table_name="customer")],
            predicates=[
                "(select count(*) from orders where c_custkey=o_custkey) > 0"
            ],
        ),
        evidence=EvidenceEnvelope(status=EvidenceStatus.SQL_ONLY),
        findings=[],
    )

    assert case_retriever.query is not None
    assert "having" in case_retriever.query.scenario_tags
    assert "aggregate" in case_retriever.query.scenario_tags
    assert "dependent_subquery" in case_retriever.query.plan_symptom_tags
    assert "count_subquery_to_exists" in case_retriever.query.root_cause_tags
    assert "having_not_pushed_down" in case_retriever.query.root_cause_tags


def test_report_builder_derives_common_rewrite_profile_without_qwen():
    class RecordingCaseRetriever:
        def __init__(self):
            self.query = None

        def retrieve(self, query: CaseRetrievalQuery, *, limit: int):
            self.query = query
            return []

    case_retriever = RecordingCaseRetriever()
    composer = OptimizationReportComposer(
        cases=[],
        case_retriever=case_retriever,
    )

    composer.compose(
        task_id="task-1",
        raw_sql=(
            "select c_custkey, count(*) from customer "
            "where (select count(*) from orders where c_custkey=o_custkey) > 0 "
            "and c_phone = null "
            "group by c_custkey having c_custkey < 100"
        ),
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(table_name="customer")],
            predicates=[
                "(select count(*) from orders where c_custkey=o_custkey) > 0 "
                "AND c_phone = NULL"
            ],
            group_by=["c_custkey"],
        ),
        evidence=EvidenceEnvelope(status=EvidenceStatus.SQL_ONLY),
        findings=[],
    )

    assert case_retriever.query is not None
    assert "aggregate" in case_retriever.query.scenario_tags
    assert "having" in case_retriever.query.scenario_tags
    assert "null_check" in case_retriever.query.scenario_tags
    assert "count_subquery_to_exists" in case_retriever.query.root_cause_tags
    assert "invalid_null_comparison" in case_retriever.query.root_cause_tags
    assert "having_not_pushed_down" in case_retriever.query.root_cause_tags


def test_report_builder_derives_implicit_cast_profile_from_ddl_without_qwen():
    class RecordingCaseRetriever:
        def __init__(self):
            self.query = None

        def retrieve(self, query: CaseRetrievalQuery, *, limit: int):
            self.query = query
            return [
                OptimizationCase(
                    case_id="implicit-case",
                    db_type="mysql",
                    sql_type="select",
                    scenario_tags=["where_filter", "equality_predicate"],
                    plan_symptom_tags=["index_not_used"],
                    root_cause_tags=["implicit_cast"],
                    case_card="implicit cast case",
                )
            ]

    case_retriever = RecordingCaseRetriever()
    composer = OptimizationReportComposer(
        cases=[],
        case_retriever=case_retriever,
    )

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from users where user_name = 123;",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(table_name="users")],
            predicates=["user_name = 123"],
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            create_tables={
                "shop.users": (
                    "CREATE TABLE `users` ("
                    "`id` bigint NOT NULL, "
                    "`user_name` varchar(64) NOT NULL, "
                    "KEY `idx_users_user_name` (`user_name`)"
                    ")"
                )
            },
        ),
        findings=[],
    )

    assert case_retriever.query is not None
    assert "where_filter" in case_retriever.query.scenario_tags
    assert "equality_predicate" in case_retriever.query.scenario_tags
    assert "index_not_used" in case_retriever.query.plan_symptom_tags
    assert "implicit_cast" in case_retriever.query.root_cause_tags
    assert [case.case_id for case in report.similar_cases] == ["implicit-case"]
