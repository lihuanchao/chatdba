import json
import re
from pathlib import Path
from typing import Protocol

import sqlglot
from sqlglot import expressions as exp

from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import (
    CaseRetrievalQuery,
    retrieve_cases_for_query,
)
from chatdba.domain.models import (
    ConfidenceLabel,
    EvidenceEnvelope,
    EvidenceStatus,
    RuleFinding,
    SqlFeatures,
)
from chatdba.domain.report_schema import (
    Bottleneck,
    IndexRecommendation,
    OptimizationReport,
    Risk,
    SqlRewrite,
)
from chatdba.workflow.case_match_reason import similar_cases_for_report
from chatdba.workflow.problem_profile import (
    SqlProblemProfile,
    build_case_retrieval_query,
    build_problem_profile_with_qwen,
    derive_problem_profile,
    merge_problem_profiles,
)

DEFAULT_SYSTEM_PROMPT = (
    "你是资深 MySQL DBA，请根据结构化证据输出 SQL优化报告。"
    "必须返回合法 JSON，并包含置信度、证据状态、瓶颈、SQL 改写、索引建议、风险和验证步骤。"
)
PROMPT_FILE = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "sql_optimization_report_prompt_zh.md"
)


class QwenReportGateway(Protocol):
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class CaseRetriever(Protocol):
    def retrieve(
        self,
        query: CaseRetrievalQuery,
        *,
        limit: int,
    ) -> list[OptimizationCase]:
        raise NotImplementedError


class OptimizationReportComposer:
    def __init__(
        self,
        *,
        cases: list[OptimizationCase],
        qwen_gateway: QwenReportGateway | None = None,
        case_retriever: CaseRetriever | None = None,
    ) -> None:
        self._cases = cases
        self._qwen_gateway = qwen_gateway
        self._case_retriever = case_retriever
        self._last_case_retrieval_debug: dict[str, object] | None = None

    @property
    def last_case_retrieval_debug(self) -> dict[str, object] | None:
        return self._last_case_retrieval_debug

    def compose(
        self,
        *,
        task_id: str,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
    ) -> OptimizationReport:
        self._last_case_retrieval_debug = None
        if self._qwen_gateway is not None:
            report = self._compose_with_qwen(
                task_id=task_id,
                raw_sql=raw_sql,
                sql_features=sql_features,
                evidence=evidence,
                findings=findings,
            )
            if report is not None:
                return report

        return self._compose_fallback(
            task_id=task_id,
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
        )

    def _compose_with_qwen(
        self,
        *,
        task_id: str,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
    ) -> OptimizationReport | None:
        system_prompt = _load_system_prompt()
        problem_profile = self._build_problem_profile(
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
            use_qwen=True,
        )
        case_query = build_case_retrieval_query(
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
            problem_profile=problem_profile,
        )
        similar_cases = self._select_cases_for_query(case_query)
        self._record_case_retrieval_debug(case_query, similar_cases)
        user_prompt = json.dumps(
            {
                "task_id": task_id,
                "raw_sql": raw_sql,
                "sql_features": sql_features.model_dump(mode="python"),
                "evidence": evidence.model_dump(mode="python"),
                "findings": [finding.model_dump(mode="python") for finding in findings],
                "problem_profile": problem_profile.model_dump(mode="python"),
                "similar_cases": [case.model_dump(mode="python") for case in similar_cases],
            },
            ensure_ascii=False,
        )
        try:
            payload = self._qwen_gateway.generate_report(system_prompt, user_prompt)
            report = OptimizationReport.model_validate(json.loads(payload))
            report.summary = _matched_rule_summary(
                raw_sql=raw_sql,
                sql_features=sql_features,
                evidence=evidence,
                findings=findings,
            )
            if not report.similar_cases and similar_cases:
                report.similar_cases = similar_cases_for_report(
                    similar_cases,
                    case_query,
                )
            return report
        except Exception:
            return None

    def _compose_fallback(
        self,
        *,
        task_id: str,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
    ) -> OptimizationReport:
        confidence, confidence_label = self._confidence_for(evidence.status)
        limitations = self._limitations_for(evidence)
        problem_profile = self._build_problem_profile(
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
            use_qwen=False,
        )
        case_query = build_case_retrieval_query(
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
            problem_profile=problem_profile,
        )
        selected_cases = self._select_cases_for_query(case_query)
        self._record_case_retrieval_debug(case_query, selected_cases)
        similar_cases = similar_cases_for_report(selected_cases, case_query)
        summary = self._build_summary(
            raw_sql=raw_sql,
            sql_features=sql_features,
            findings=findings,
            evidence=evidence,
        )
        sql_rewrites = self._build_sql_rewrites(raw_sql, findings)
        index_recommendations = self._build_index_recommendations(sql_features, findings)
        risks = self._build_risks(evidence.status)
        validation_steps = self._build_validation_steps(evidence.status)

        return OptimizationReport(
            task_id=task_id,
            summary=summary,
            confidence=confidence,
            confidence_label=confidence_label,
            evidence_status=evidence.status,
            missing_evidence=evidence.missing_evidence,
            limitations=limitations,
            bottlenecks=[
                Bottleneck(code=finding.code, evidence=finding.message)
                for finding in findings
            ],
            sql_rewrites=sql_rewrites,
            index_recommendations=index_recommendations,
            risks=risks,
            validation_steps=validation_steps,
            similar_cases=similar_cases,
        )

    def _select_cases(
        self,
        *,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
        problem_profile: SqlProblemProfile | None = None,
    ) -> list[OptimizationCase]:
        query = build_case_retrieval_query(
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
            problem_profile=problem_profile,
        )
        return self._select_cases_for_query(query)

    def _select_cases_for_query(
        self,
        query: CaseRetrievalQuery,
    ) -> list[OptimizationCase]:
        if self._case_retriever is not None:
            return self._case_retriever.retrieve(query, limit=3)
        return retrieve_cases_for_query(
            self._cases,
            query,
            limit=3,
        )

    def _build_problem_profile(
        self,
        *,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
        use_qwen: bool,
    ) -> SqlProblemProfile:
        base_profile = derive_problem_profile(
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
        )
        if not use_qwen or self._qwen_gateway is None:
            return base_profile

        qwen_profile = build_problem_profile_with_qwen(
            qwen_gateway=self._qwen_gateway,
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
        )
        if qwen_profile is None:
            return base_profile
        return merge_problem_profiles(base_profile, qwen_profile)

    def _record_case_retrieval_debug(
        self,
        query: CaseRetrievalQuery,
        cases: list[OptimizationCase],
    ) -> None:
        similar_cases = similar_cases_for_report(cases, query)
        self._last_case_retrieval_debug = {
            "query": {
                "db_type": query.db_type,
                "db_version_major": query.db_version_major,
                "sql_type": query.sql_type,
                "scenario_tags": query.scenario_tags,
                "plan_symptom_tags": query.plan_symptom_tags,
                "root_cause_tags": query.root_cause_tags,
            },
            "matched_cases": [
                {
                    "case_id": case.case_id,
                    "reason": case.reason,
                }
                for case in similar_cases
            ],
        }

    def _confidence_for(
        self,
        status: EvidenceStatus,
    ) -> tuple[float, ConfidenceLabel]:
        if status == EvidenceStatus.FULL:
            return 0.9, ConfidenceLabel.HIGH
        if status == EvidenceStatus.PARTIAL:
            return 0.65, ConfidenceLabel.MEDIUM
        return 0.35, ConfidenceLabel.LOW

    def _limitations_for(self, evidence: EvidenceEnvelope) -> list[str]:
        limitations = list(evidence.collection_errors)
        if evidence.status == EvidenceStatus.SQL_ONLY:
            limitations.insert(0, "未获取到源库执行证据，报告基于 SQL 文本、规则和历史案例生成。")
        elif evidence.status == EvidenceStatus.PARTIAL:
            limitations.insert(0, "仅获取到部分源库证据，建议补齐后再执行变更。")
        return limitations

    def _build_summary(
        self,
        *,
        raw_sql: str,
        sql_features: SqlFeatures,
        findings: list[RuleFinding],
        evidence: EvidenceEnvelope,
    ) -> str:
        return _matched_rule_summary(
            raw_sql=raw_sql,
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
        )

    def _build_sql_rewrites(
        self,
        raw_sql: str,
        findings: list[RuleFinding],
    ) -> list[SqlRewrite]:
        sql = raw_sql.strip().rstrip(";")
        if any(finding.code == "limit_with_order_by" for finding in findings):
            return [
                SqlRewrite(
                    title="减少返回列并保持排序列可走索引",
                    sql=_rewrite_select_star(sql),
                )
            ]
        if re.search(r"^\s*select\s+\*", raw_sql, flags=re.IGNORECASE):
            return [
                SqlRewrite(
                    title="避免 SELECT *，只返回必要列",
                    sql=_rewrite_select_star(sql),
                )
            ]
        return [
            SqlRewrite(
                title="保持语义不变的可读性重写",
                sql=sql,
            )
        ]

    def _build_index_recommendations(
        self,
        sql_features: SqlFeatures,
        findings: list[RuleFinding],
    ) -> list[IndexRecommendation]:
        if sql_features.order_by and any(
            finding.code == "limit_with_order_by" for finding in findings
        ):
            table_name = (
                sql_features.tables[0].table_name
                if sql_features.tables
                else "target_table"
            )
            index_columns = _recommended_index_columns(sql_features)
            column_suffix = "_".join(index_columns) if index_columns else "order"
            return [
                IndexRecommendation(
                    ddl=(
                        f"CREATE INDEX idx_{table_name}_{column_suffix} "
                        f"ON {table_name}({', '.join(index_columns)});"
                    ),
                    risk="medium",
                )
            ]
        table_name = (
            sql_features.tables[0].table_name if sql_features.tables else "target_table"
        )
        return [
            IndexRecommendation(
                ddl=(
                    f"CREATE INDEX idx_{table_name}_opt "
                    f"ON {table_name}(<where_col1>, <where_col2>);"
                ),
                risk="medium",
            )
        ]

    def _build_risks(self, status: EvidenceStatus) -> list[Risk]:
        if status == EvidenceStatus.SQL_ONLY:
            return [
                Risk(
                    level="medium",
                    description="当前建议未经过源库执行计划验证，请先在测试环境验证后上线。",
                )
            ]
        return []

    def _build_validation_steps(self, status: EvidenceStatus) -> list[str]:
        if status == EvidenceStatus.FULL:
            return [
                "对重写 SQL 执行 EXPLAIN FORMAT=JSON，确认访问路径和预估行数改善。",
            ]
        if status == EvidenceStatus.PARTIAL:
            return [
                "先补齐缺失证据（EXPLAIN/DDL），再评审并执行索引或 SQL 变更。",
            ]
        return [
            "先在目标库测试环境执行 EXPLAIN 与回归测试，再决定是否上线。",
        ]


def _recommended_index_columns(sql_features: SqlFeatures) -> list[str]:
    if not sql_features.tables:
        return ["<where_col1>", "<where_col2>"]

    target = sql_features.tables[0]
    table_alias = target.alias or target.table_name
    columns: list[str] = []
    for predicate in sql_features.predicates:
        columns.extend(_qualified_columns_for(predicate, table_alias, target.table_name))
    for order_by in sql_features.order_by:
        column = _first_qualified_column_for(order_by, table_alias, target.table_name)
        if column:
            columns.append(column)
    for join in sql_features.joins:
        columns.extend(_qualified_columns_for(join, table_alias, target.table_name))

    unique_columns: list[str] = []
    for column in columns:
        if column not in unique_columns:
            unique_columns.append(column)
    return unique_columns or ["<where_col1>", "<where_col2>"]


def _qualified_columns_for(expression: str, alias: str, table_name: str) -> list[str]:
    qualifiers = {alias, table_name}
    columns: list[str] = []
    for qualifier, column in re.findall(
        r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b",
        expression,
    ):
        if qualifier in qualifiers:
            columns.append(column)
    if columns:
        return columns

    # Single-table SQL often has unqualified columns in WHERE/ORDER BY.
    if "." not in expression:
        return [
            column
            for column in re.findall(
                r"\b([A-Za-z_][\w]*)\s*(?:=|>|<|>=|<=|IN\b|LIKE\b)",
                expression,
                re.IGNORECASE,
            )
            if column.upper() not in {"AND", "OR", "NOT"}
        ]
    return []


def _first_qualified_column_for(
    expression: str,
    alias: str,
    table_name: str,
) -> str | None:
    columns = _qualified_columns_for(expression, alias, table_name)
    if columns:
        return columns[0]
    match = re.search(r"\b([A-Za-z_][\w]*)\b", expression)
    if match:
        return match.group(1)
    return None


def _rewrite_select_star(sql: str) -> str:
    rewritten = re.sub(
        r"^\s*select\s+\*",
        "SELECT <必要列>",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    return rewritten if rewritten else sql


RULE_SUMMARY_TEXT: dict[str, str] = {
    "rule01": (
        "**rule01. 投影下推（Projection Pushdown）**\n"
        "仅返回外部查询中实际需要的列，减少不必要的数据传递，仅 SELECT * 时命中该规则。"
    ),
    "rule02": (
        "**rule02. 选择条件下推（Selection Pushdown）**\n"
        "将 WHERE 条件尽量下推到子查询或表的级别，过滤掉不必要的数据。"
    ),
    "rule03": (
        "**rule03. 连接条件优化（Join Optimization）**\n"
        "减少连接的数据量或提高连接顺序的效率。"
    ),
    "rule04": (
        "**rule04. 索引优化（Index Optimization）**\n"
        "确保列上有适合的索引，提高数据访问速度。"
    ),
    "rule06": (
        "**rule06. EXISTS 与 IN 优化（Subquery Optimization）**\n"
        "根据场景选用 EXISTS 或 IN，避免子查询返回大量数据影响效率。"
    ),
    "rule07": (
        "**rule07. 聚合、排序下推（Aggregation and Sorting Pushdown）**\n"
        "在子查询中提前聚合或排序，减少 GROUP BY、ORDER BY 的行数处理。"
    ),
    "rule08": (
        "**rule08. 避免函数操作列（Avoid Functions on Columns）**\n"
        "在 WHERE 条件中避免对列使用函数，否则会阻止索引发挥作用。"
    ),
    "rule09": (
        "**rule09. 分组优化（GROUP BY Optimization）**\n"
        "优化分组操作，提前减少无关数据或者替换复杂计算。"
    ),
    "rule10": (
        "**rule10. 重复或太复杂的子查询优化（Subquery Deduplication）**\n"
        "对重复出现的子查询进行合并，或分解为更高效的查询。"
    ),
    "rule11": (
        "**rule11. 避免使用 ORDER BY RAND()**\n"
        "ORDER BY RAND() 会对全表的每行调用随机函数，容易造成严重性能问题。"
    ),
    "rule12": (
        "**rule12. 使用 LIMIT 限制数据量**\n"
        "避免对全表操作，适当地限制返回的行数。"
    ),
    "rule13": (
        "**rule13. 避免多表嵌套和太复杂的嵌套查询**\n"
        "尽量简化联表操作逻辑，避免深度嵌套。"
    ),
    "rule15": (
        "**rule15. 索引列上的运算导致索引失效**\n"
        "索引列上的运算会导致索引失效，应尽量将索引列上的运算转换到常量端。"
    ),
    "rule16": (
        "**rule16. 隐式类型转换导致索引失效**\n"
        "查询条件中的数据类型和表列数据类型不一致时，可能发生隐式类型转换并导致索引失效。"
    ),
    "rule17": (
        "**rule17. IN子查询重写优化**\n"
        "IN 子查询可在满足条件时改写为等价的 EXISTS 子查询或内关联。"
    ),
}


def _matched_rule_summary(
    *,
    raw_sql: str,
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> str:
    rules = _matched_rule_ids(
        raw_sql=raw_sql,
        sql_features=sql_features,
        evidence=evidence,
        findings=findings,
    )
    if not rules:
        return "无匹配规则"
    return "\n\n".join(RULE_SUMMARY_TEXT[rule] for rule in rules)


def _matched_rule_ids(
    *,
    raw_sql: str,
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> list[str]:
    expression = _parse_sql_expression(raw_sql)
    candidates: list[str] = []
    finding_codes = {finding.code for finding in findings}
    explain_text = json.dumps(evidence.explain_json or {}, ensure_ascii=False).lower()

    if expression is not None and _outer_select_has_star_projection(expression):
        candidates.append("rule01")
    if expression is not None and _has_subquery_with_outer_where(expression):
        candidates.append("rule02")
    if sql_features.joins or (expression is not None and _has_join_without_on(expression)):
        candidates.append("rule03")
    if (
        finding_codes & {"limit_with_order_by", "full_table_scan"}
        or "filesort" in explain_text
    ):
        candidates.append("rule04")
    if expression is not None and _has_exists_or_in_subquery(expression):
        candidates.append("rule06")
    if sql_features.group_by or sql_features.order_by:
        candidates.append("rule07")
    if expression is not None and _has_function_wrapped_where_column(expression):
        candidates.append("rule08")
    if sql_features.group_by:
        candidates.append("rule09")
    if expression is not None and _has_multiple_subqueries(expression):
        candidates.append("rule10")
    if expression is not None and _has_order_by_rand(expression):
        candidates.append("rule11")
    if sql_features.has_limit:
        candidates.append("rule12")
    if expression is not None and _has_nested_subquery(expression):
        candidates.append("rule13")
    if expression is not None and _has_arithmetic_on_where_column(expression):
        candidates.append("rule15")
    if finding_codes & {"implicit_cast"}:
        candidates.append("rule16")
    if expression is not None and _has_in_subquery(expression):
        candidates.append("rule17")

    return _ordered_unique(candidates)


def _parse_sql_expression(raw_sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(raw_sql, read="mysql")
    except Exception:
        return None


def _outer_select_has_star_projection(expression: exp.Expression) -> bool:
    select = _outer_select(expression)
    if select is None:
        return False
    return any(_projection_is_star(projection) for projection in select.expressions)


def _outer_select(expression: exp.Expression) -> exp.Select | None:
    if isinstance(expression, exp.Select):
        return expression
    if isinstance(expression, exp.Union):
        left = expression.this
        return left if isinstance(left, exp.Select) else None
    return expression.find(exp.Select)


def _projection_is_star(projection: exp.Expression) -> bool:
    if isinstance(projection, exp.Star):
        return True
    return isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)


def _has_subquery_with_outer_where(expression: exp.Expression) -> bool:
    outer = _outer_select(expression)
    return bool(outer and outer.args.get("where") and list(expression.find_all(exp.Subquery)))


def _has_join_without_on(expression: exp.Expression) -> bool:
    return any(
        not join.args.get("on")
        for join in expression.find_all(exp.Join)
    )


def _has_exists_or_in_subquery(expression: exp.Expression) -> bool:
    return any(expression.find_all(exp.Exists)) or _has_in_subquery(expression)


def _has_in_subquery(expression: exp.Expression) -> bool:
    return any(in_expr.args.get("query") is not None for in_expr in expression.find_all(exp.In))


def _has_multiple_subqueries(expression: exp.Expression) -> bool:
    return len(list(expression.find_all(exp.Subquery))) > 1


def _has_nested_subquery(expression: exp.Expression) -> bool:
    for subquery in expression.find_all(exp.Subquery):
        inner = subquery.this
        if inner is not None and any(inner.find_all(exp.Subquery)):
            return True
    return False


def _has_order_by_rand(expression: exp.Expression) -> bool:
    order = expression.find(exp.Order)
    return bool(order and list(order.find_all(exp.Rand)))


def _has_function_wrapped_where_column(expression: exp.Expression) -> bool:
    where = expression.find(exp.Where)
    if where is None:
        return False
    for column in where.find_all(exp.Column):
        node = column.parent
        while node is not None and not isinstance(node, exp.Where):
            if isinstance(node, exp.Func):
                return True
            node = node.parent
    return False


def _has_arithmetic_on_where_column(expression: exp.Expression) -> bool:
    where = expression.find(exp.Where)
    if where is None:
        return False
    arithmetic_types = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
    for column in where.find_all(exp.Column):
        node = column.parent
        while node is not None and not isinstance(node, exp.Where):
            if isinstance(node, arithmetic_types):
                return True
            node = node.parent
    return False


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _load_system_prompt() -> str:
    try:
        content = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    if not content:
        return DEFAULT_SYSTEM_PROMPT
    return content
