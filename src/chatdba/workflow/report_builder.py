import json
import re
from pathlib import Path
from typing import Protocol

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
        summary = self._build_summary(findings, evidence.status)
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
        findings: list[RuleFinding],
        status: EvidenceStatus,
    ) -> str:
        if findings:
            return findings[0].message
        if status == EvidenceStatus.SQL_ONLY:
            return "当前为 SQL-only 分析：基于 SQL 文本、规则与历史案例给出优化建议。"
        if status == EvidenceStatus.PARTIAL:
            return "当前为部分证据分析：结合已采集证据与 SQL 规则给出优化建议。"
        return "当前为完整证据分析：基于执行计划、表结构与 SQL 规则生成建议。"

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


def _load_system_prompt() -> str:
    try:
        content = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    if not content:
        return DEFAULT_SYSTEM_PROMPT
    return content
