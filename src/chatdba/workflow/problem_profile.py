import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from chatdba.cases.retriever import CaseRetrievalQuery
from chatdba.domain.models import EvidenceEnvelope, RuleFinding, SqlFeatures

PROFILE_PROMPT_FILE = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "sql_problem_profile_prompt_zh.md"
)
DEFAULT_PROFILE_SYSTEM_PROMPT = (
    "你是资深 MySQL DBA，请根据 SQL、表结构、执行计划和规则发现生成 SQL问题画像。"
    "必须只返回合法 JSON。"
)

ALLOWED_SCENARIO_TAGS = {
    "aggregate",
    "any_all_subquery",
    "delete_all",
    "derived_table",
    "distinct",
    "exists_subquery",
    "function_predicate",
    "having",
    "in_subquery",
    "join",
    "group_by",
    "left_join",
    "max_min_subquery",
    "not_in_subquery",
    "null_check",
    "order_by",
    "or_predicate",
    "projection",
    "limit",
    "where_filter",
    "equality_predicate",
    "range_predicate",
    "subquery",
    "union",
}
ALLOWED_PLAN_SYMPTOM_TAGS = {
    "all",
    "dependent_subquery",
    "eq_ref",
    "ref",
    "range",
    "index",
    "index_not_used",
    "index_merge",
    "materialized_derived",
    "nested_loop",
    "using_filesort",
    "using_temporary",
    "using_join_buffer",
}
ALLOWED_ROOT_CAUSE_TAGS = {
    "all_subquery_null_semantics",
    "count_subquery_to_exists",
    "deep_pagination",
    "delete_all_without_truncate",
    "distinct_in_exists_subquery",
    "exists_subquery_not_decorrelated",
    "group_by_implicit_sort",
    "group_by_not_indexed",
    "group_by_mixed_tables",
    "having_not_pushed_down",
    "high_back_to_table_cost",
    "implicit_cast",
    "index_invalidated_by_function",
    "invalid_null_comparison",
    "join_elimination",
    "limit_not_pushed_to_union",
    "max_min_aggregate_subquery",
    "missing_join_index",
    "missing_composite_index",
    "missing_index",
    "npe_aggregate",
    "null_sensitive_not_in",
    "or_predicate_index_merge",
    "order_by_mixed_tables",
    "outer_join_null_rejected",
    "predicate_not_pushed_down",
    "projection_not_pushed_down",
    "query_folding",
    "subquery_order_by_without_limit",
    "stats_stale",
    "wrong_driving_table",
}
STRING_COLUMN_TYPES = {
    "char",
    "varchar",
    "tinytext",
    "text",
    "mediumtext",
    "longtext",
}


class ProblemProfileGateway(Protocol):
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class SqlProblemProfile(BaseModel):
    scenario_tags: list[str] = Field(default_factory=list)
    plan_symptom_tags: list[str] = Field(default_factory=list)
    root_cause_tags: list[str] = Field(default_factory=list)
    problem_summary: str = ""
    confidence: str = "low"
    evidence: list[str] = Field(default_factory=list)


def derive_problem_profile(
    *,
    raw_sql: str,
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> SqlProblemProfile:
    scenario_tags = scenario_tags_for(sql_features)
    normalized_sql = " ".join(raw_sql.lower().split())
    if sql_features.predicates:
        scenario_tags = merge_unique(scenario_tags, ["where_filter"])
    if _has_equality_predicate(sql_features):
        scenario_tags = merge_unique(scenario_tags, ["equality_predicate"])
    if _has_aggregate_function(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["aggregate"])
    if _has_having_clause(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["having"])
    if _has_not_in_subquery(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["not_in_subquery", "subquery"])
    if _has_count_subquery_exists_check(raw_sql):
        scenario_tags = merge_unique(
            scenario_tags,
            ["subquery", "aggregate", "exists_subquery"],
        )
    if re.search(r"(?:^|\s)or(?:\s|$)", normalized_sql):
        scenario_tags = merge_unique(scenario_tags, ["or_predicate"])
    if _has_invalid_null_comparison(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["null_check"])

    plan_symptom_tags = plan_symptom_tags_for(evidence, findings)
    root_cause_tags = root_cause_tags_for(findings)
    profile = SqlProblemProfile(
        scenario_tags=scenario_tags,
        plan_symptom_tags=plan_symptom_tags,
        root_cause_tags=root_cause_tags,
        problem_summary="",
        confidence="low",
        evidence=[],
    )

    implicit_cast_evidence = _implicit_cast_evidence(sql_features, evidence)
    if implicit_cast_evidence:
        profile.scenario_tags = merge_unique(
            profile.scenario_tags,
            ["where_filter", "equality_predicate"],
        )
        profile.plan_symptom_tags = merge_unique(
            profile.plan_symptom_tags,
            ["index_not_used"],
        )
        profile.root_cause_tags = merge_unique(
            profile.root_cause_tags,
            ["implicit_cast"],
        )
        profile.problem_summary = "字符串列与数字字面量比较，可能触发 MySQL 隐式类型转换并导致索引失效。"
        profile.confidence = "high"
        profile.evidence = implicit_cast_evidence

    rewrite_profile = _deterministic_rewrite_profile(raw_sql)
    profile = merge_problem_profiles(profile, rewrite_profile)

    return sanitize_problem_profile(profile)


def build_problem_profile_with_qwen(
    *,
    qwen_gateway: ProblemProfileGateway,
    raw_sql: str,
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> SqlProblemProfile | None:
    user_prompt = json.dumps(
        {
            "raw_sql": raw_sql,
            "sql_features": sql_features.model_dump(mode="python"),
            "evidence": evidence.model_dump(mode="python"),
            "findings": [finding.model_dump(mode="python") for finding in findings],
        },
        ensure_ascii=False,
    )
    try:
        with _usage_operation(qwen_gateway, "sql_problem_profile"):
            payload = qwen_gateway.generate_report(
                load_problem_profile_prompt(),
                user_prompt,
            )
        return sanitize_problem_profile(
            SqlProblemProfile.model_validate(json.loads(payload))
        )
    except Exception:
        return None


def merge_problem_profiles(
    base_profile: SqlProblemProfile,
    qwen_profile: SqlProblemProfile,
) -> SqlProblemProfile:
    return sanitize_problem_profile(
        SqlProblemProfile(
            scenario_tags=merge_unique(
                base_profile.scenario_tags,
                qwen_profile.scenario_tags,
            ),
            plan_symptom_tags=merge_unique(
                base_profile.plan_symptom_tags,
                qwen_profile.plan_symptom_tags,
            ),
            root_cause_tags=merge_unique(
                base_profile.root_cause_tags,
                qwen_profile.root_cause_tags,
            ),
            problem_summary=qwen_profile.problem_summary or base_profile.problem_summary,
            confidence=qwen_profile.confidence or base_profile.confidence,
            evidence=merge_unique(base_profile.evidence, qwen_profile.evidence),
        )
    )


def sanitize_problem_profile(profile: SqlProblemProfile) -> SqlProblemProfile:
    confidence = normalize_tag(profile.confidence or "low")
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    return SqlProblemProfile(
        scenario_tags=_filter_allowed_tags(profile.scenario_tags, ALLOWED_SCENARIO_TAGS),
        plan_symptom_tags=_filter_allowed_tags(
            profile.plan_symptom_tags,
            ALLOWED_PLAN_SYMPTOM_TAGS,
        ),
        root_cause_tags=_filter_allowed_tags(
            profile.root_cause_tags,
            ALLOWED_ROOT_CAUSE_TAGS,
        ),
        problem_summary=profile.problem_summary.strip(),
        confidence=confidence,
        evidence=[item.strip() for item in profile.evidence if item.strip()],
    )


def build_case_retrieval_query(
    *,
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
    problem_profile: SqlProblemProfile | None = None,
) -> CaseRetrievalQuery:
    db_type = _db_type_for(evidence)
    db_version_major = _db_version_major_for(evidence)
    sql_type = sql_features.statement_type
    scenario_tags = scenario_tags_for(sql_features)
    plan_symptom_tags = plan_symptom_tags_for(evidence, findings)
    root_cause_tags = root_cause_tags_for(findings)
    if problem_profile is not None:
        scenario_tags = merge_unique(scenario_tags, problem_profile.scenario_tags)
        plan_symptom_tags = merge_unique(
            plan_symptom_tags,
            problem_profile.plan_symptom_tags,
        )
        root_cause_tags = merge_unique(root_cause_tags, problem_profile.root_cause_tags)
    return CaseRetrievalQuery(
        db_type=db_type,
        db_version_major=db_version_major,
        sql_type=sql_type,
        scenario_tags=scenario_tags,
        plan_symptom_tags=plan_symptom_tags,
        root_cause_tags=root_cause_tags,
        embedding_text=_case_query_embedding_text(
            db_type=db_type,
            db_version_major=db_version_major,
            sql_type=sql_type,
            scenario_tags=scenario_tags,
            plan_symptom_tags=plan_symptom_tags,
            root_cause_tags=root_cause_tags,
            sql_features=sql_features,
            problem_profile=problem_profile,
        ),
    )


def scenario_tags_for(sql_features: SqlFeatures) -> list[str]:
    tags: list[str] = []
    if sql_features.joins:
        tags.append("join")
    if sql_features.group_by:
        tags.append("group_by")
    if sql_features.order_by:
        tags.append("order_by")
    if sql_features.has_limit:
        tags.append("limit")
    return tags


def plan_symptom_tags_for(
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> list[str]:
    tags: set[str] = set()
    for finding in findings:
        tags.update(_plan_symptoms_from_finding(finding.code))
    if evidence.explain_json:
        tags.update(_collect_plan_terms(evidence.explain_json))
    return sorted(tags)


def root_cause_tags_for(findings: list[RuleFinding]) -> list[str]:
    tags: set[str] = set()
    for finding in findings:
        tags.update(_root_causes_from_finding(finding.code))
    return sorted(tags)


def normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def merge_unique(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*left, *right]:
        if item and item not in merged:
            merged.append(item)
    return merged


def load_problem_profile_prompt() -> str:
    try:
        content = PROFILE_PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_PROFILE_SYSTEM_PROMPT
    if not content:
        return DEFAULT_PROFILE_SYSTEM_PROMPT
    return content


def _usage_operation(qwen_gateway: ProblemProfileGateway, operation: str):
    usage_operation = getattr(qwen_gateway, "usage_operation", None)
    if callable(usage_operation):
        return usage_operation(operation)
    return _NoopUsageOperation()


class _NoopUsageOperation:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def _db_type_for(evidence: EvidenceEnvelope) -> str:
    if evidence.route and evidence.route.db_type:
        return evidence.route.db_type
    return "mysql"


def _db_version_major_for(evidence: EvidenceEnvelope) -> str | None:
    if evidence.route is None or not evidence.route.version:
        return None
    version = evidence.route.version.strip()
    match = re.match(r"^(\d+)(?:\.(\d+))?", version)
    if not match:
        return version
    if match.group(2):
        return f"{match.group(1)}.{match.group(2)}"
    return match.group(1)


def _case_query_embedding_text(
    *,
    db_type: str,
    db_version_major: str | None,
    sql_type: str | None,
    scenario_tags: list[str],
    plan_symptom_tags: list[str],
    root_cause_tags: list[str],
    sql_features: SqlFeatures,
    problem_profile: SqlProblemProfile | None = None,
) -> str:
    parts = [
        db_type,
        db_version_major or "",
        sql_type or "",
        " ".join(scenario_tags),
        " ".join(plan_symptom_tags),
        " ".join(root_cause_tags),
        f"tables={len(sql_features.tables or [])}",
        "predicates=" + " | ".join(sql_features.predicates or []),
        "joins=" + " | ".join(sql_features.joins or []),
        "order_by=" + " | ".join(sql_features.order_by or []),
    ]
    if problem_profile is not None:
        parts.extend(
            [
                problem_profile.problem_summary,
                "profile_evidence=" + " | ".join(problem_profile.evidence),
            ]
        )
    return " ".join(part for part in parts if part).strip()


def _plan_symptoms_from_finding(code: str) -> set[str]:
    normalized = normalize_tag(code)
    mapping = {
        "limit_with_order_by": {"using_filesort"},
        "full_table_scan": {"all"},
        "temporary_table": {"using_temporary"},
    }
    return mapping.get(normalized, {normalized} if normalized else set())


def _root_causes_from_finding(code: str) -> set[str]:
    normalized = normalize_tag(code)
    mapping = {
        "limit_with_order_by": {"missing_composite_index"},
        "full_table_scan": {"missing_index"},
        "implicit_cast": {"implicit_cast"},
        "wrong_driving_table": {"wrong_driving_table"},
    }
    return mapping.get(normalized, {normalized} if normalized else set())


def _collect_plan_terms(value: object) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = normalize_tag(str(key))
            if normalized_key in {
                "using_filesort",
                "using_temporary",
                "using_temporary_table",
                "using_join_buffer",
            } and nested:
                if normalized_key == "using_temporary_table":
                    terms.add("using_temporary")
                else:
                    terms.add(normalized_key)
            if normalized_key in {"access_type", "join_type", "node_type"} and isinstance(
                nested,
                str,
            ):
                terms.add(normalize_tag(nested))
            terms.update(_collect_plan_terms(nested))
    elif isinstance(value, list):
        for item in value:
            terms.update(_collect_plan_terms(item))
    elif isinstance(value, str):
        normalized = normalize_tag(value)
        if normalized in {
            "all",
            "range",
            "ref",
            "index_merge",
            "using_filesort",
            "using_temporary",
            "seq_scan",
            "nested_loop",
            "hash_join",
            "bitmap_heap_scan",
            "sort",
            "materialize",
        }:
            terms.add(normalized)
    return terms


def _deterministic_rewrite_profile(raw_sql: str) -> SqlProblemProfile:
    scenario_tags: list[str] = []
    plan_symptom_tags: list[str] = []
    root_cause_tags: list[str] = []
    evidence: list[str] = []

    if _has_count_subquery_exists_check(raw_sql):
        scenario_tags = merge_unique(
            scenario_tags,
            ["subquery", "aggregate", "exists_subquery"],
        )
        plan_symptom_tags = merge_unique(plan_symptom_tags, ["dependent_subquery"])
        root_cause_tags = merge_unique(root_cause_tags, ["count_subquery_to_exists"])
        evidence.append("存在 COUNT(*) 标量子查询与 > 0 存在性判断。")

    if _has_invalid_null_comparison(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["where_filter", "null_check"])
        root_cause_tags = merge_unique(root_cause_tags, ["invalid_null_comparison"])
        evidence.append("SQL 使用 = NULL 或 <> NULL 判断空值。")

    having_clause = _having_clause(raw_sql)
    if having_clause is not None:
        scenario_tags = merge_unique(scenario_tags, ["having"])
        if not _has_aggregate_function(having_clause):
            root_cause_tags = merge_unique(root_cause_tags, ["having_not_pushed_down"])
            evidence.append("HAVING 条件不包含聚合函数，可评估下推到 WHERE。")

    if _has_not_in_subquery(raw_sql):
        scenario_tags = merge_unique(scenario_tags, ["not_in_subquery", "subquery"])
        root_cause_tags = merge_unique(root_cause_tags, ["null_sensitive_not_in"])
        evidence.append("存在 NOT IN 子查询，需确认子查询选择列是否可能为 NULL。")

    if _has_function_wrapped_predicate(raw_sql):
        scenario_tags = merge_unique(
            scenario_tags,
            ["where_filter", "function_predicate"],
        )
        plan_symptom_tags = merge_unique(plan_symptom_tags, ["index_not_used"])
        root_cause_tags = merge_unique(
            root_cause_tags,
            ["index_invalidated_by_function"],
        )
        evidence.append("过滤谓词中存在函数包裹列的形态，可能导致索引不可用。")

    problem_summary = "；".join(evidence)
    confidence = "medium" if evidence else "low"
    return sanitize_problem_profile(
        SqlProblemProfile(
            scenario_tags=scenario_tags,
            plan_symptom_tags=plan_symptom_tags,
            root_cause_tags=root_cause_tags,
            problem_summary=problem_summary,
            confidence=confidence,
            evidence=evidence,
        )
    )


def _has_aggregate_function(sql: str) -> bool:
    return bool(re.search(r"\b(?:count|sum|avg|min|max)\s*\(", sql, flags=re.IGNORECASE))


def _has_having_clause(sql: str) -> bool:
    return _having_clause(sql) is not None


def _having_clause(sql: str) -> str | None:
    match = re.search(
        r"\bhaving\b(?P<having>.*?)(?:\border\s+by\b|\blimit\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return match.group("having").strip()


def _has_count_subquery_exists_check(sql: str) -> bool:
    return bool(
        re.search(
            r"\(\s*select\s+count\s*\(\s*\*\s*\).*?\)\s*>\s*0\b",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        or re.search(
            r"\b0\s*<\s*\(\s*select\s+count\s*\(\s*\*\s*\).*?\)",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _has_invalid_null_comparison(sql: str) -> bool:
    return bool(
        re.search(
            r"(?:=|<>|!=)\s*null\b|\bnull\s*(?:=|<>|!=)",
            sql,
            flags=re.IGNORECASE,
        )
    )


def _has_not_in_subquery(sql: str) -> bool:
    return bool(
        re.search(
            r"\bnot\s+in\s*\(\s*select\b",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _has_function_wrapped_predicate(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(?:date|year|month|day|left|right|substring|substr|adddate|date_add|date_sub|lower|upper)\s*\(\s*(?:`?[A-Za-z_][\w]*`?\.)?`?[A-Za-z_][\w]*`?",
            sql,
            flags=re.IGNORECASE,
        )
    )


def _has_equality_predicate(sql_features: SqlFeatures) -> bool:
    return any(
        re.search(r"(?<![<>=!])=(?![=])", predicate)
        for predicate in sql_features.predicates
    )


def _implicit_cast_evidence(
    sql_features: SqlFeatures,
    evidence: EvidenceEnvelope,
) -> list[str]:
    column_types = _column_types_from_create_tables(evidence.create_tables)
    if not column_types:
        return []

    findings: list[str] = []
    for predicate in sql_features.predicates:
        for column in _numeric_literal_comparison_columns(predicate):
            data_type = column_types.get(column.lower())
            if data_type in STRING_COLUMN_TYPES:
                findings.append(
                    f"{column} 类型为 {data_type}，但谓词 {predicate} 使用数字字面量比较。"
                )
    return merge_unique([], findings)


def _column_types_from_create_tables(create_tables: dict[str, str]) -> dict[str, str]:
    column_types: dict[str, str] = {}
    for ddl in create_tables.values():
        for match in re.finditer(
            r"`(?P<column>[^`]+)`\s+(?P<data_type>[A-Za-z]+)",
            ddl,
            flags=re.IGNORECASE,
        ):
            column = match.group("column").lower()
            data_type = match.group("data_type").lower()
            column_types[column] = data_type
    return column_types


def _numeric_literal_comparison_columns(predicate: str) -> list[str]:
    patterns = [
        r"(?:`?[A-Za-z_][\w]*`?\.)?`?(?P<column>[A-Za-z_][\w]*)`?\s*=\s*[+-]?\d+(?:\.\d+)?\b",
        r"\b[+-]?\d+(?:\.\d+)?\s*=\s*(?:`?[A-Za-z_][\w]*`?\.)?`?(?P<column>[A-Za-z_][\w]*)`?",
        r"(?:`?[A-Za-z_][\w]*`?\.)?`?(?P<column>[A-Za-z_][\w]*)`?\s+in\s*\(\s*[+-]?\d+(?:\.\d+)?(?:\s*,\s*[+-]?\d+(?:\.\d+)?)*\s*\)",
    ]
    columns: list[str] = []
    for pattern in patterns:
        columns.extend(
            match.group("column")
            for match in re.finditer(pattern, predicate, flags=re.IGNORECASE)
        )
    return merge_unique([], columns)


def _filter_allowed_tags(values: list[str], allowed_tags: set[str]) -> list[str]:
    tags: list[str] = []
    for value in values:
        normalized = normalize_tag(value)
        if normalized in allowed_tags and normalized not in tags:
            tags.append(normalized)
    return tags
