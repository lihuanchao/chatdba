import json
import re
from pathlib import Path
from typing import Protocol

from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases
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
    SimilarCase,
    SqlRewrite,
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


class OptimizationReportComposer:
    def __init__(
        self,
        *,
        cases: list[OptimizationCase],
        qwen_gateway: QwenReportGateway | None = None,
    ) -> None:
        self._cases = cases
        self._qwen_gateway = qwen_gateway

    def compose(
        self,
        *,
        task_id: str,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
    ) -> OptimizationReport:
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
        similar_cases = self._select_cases(
            sql_features=sql_features,
            evidence=evidence,
            findings=findings,
        )
        user_prompt = json.dumps(
            {
                "task_id": task_id,
                "raw_sql": raw_sql,
                "sql_features": sql_features.model_dump(mode="python"),
                "evidence": evidence.model_dump(mode="python"),
                "findings": [finding.model_dump(mode="python") for finding in findings],
                "similar_cases": [case.model_dump(mode="python") for case in similar_cases],
            },
            ensure_ascii=False,
        )
        try:
            payload = self._qwen_gateway.generate_report(system_prompt, user_prompt)
            return OptimizationReport.model_validate(json.loads(payload))
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
        similar_cases = [
            SimilarCase(case_id=case.case_id, reason=case.case_card)
            for case in self._select_cases(
                sql_features=sql_features,
                evidence=evidence,
                findings=findings,
            )
        ]
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
    ) -> list[OptimizationCase]:
        return retrieve_cases(
            self._cases,
            db_type=_db_type_for(evidence),
            db_version_major=_db_version_major_for(evidence),
            sql_type=sql_features.statement_type,
            scenario_tags=_scenario_tags_for(sql_features),
            plan_symptom_tags=_plan_symptom_tags_for(evidence, findings),
            root_cause_tags=_root_cause_tags_for(findings),
            limit=3,
        )

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
            order_column = sql_features.order_by[0].split()[0]
            table_name = sql_features.tables[0].table_name if sql_features.tables else "target_table"
            return [
                IndexRecommendation(
                    ddl=f"CREATE INDEX idx_{table_name}_{order_column.lower()} ON {table_name}({order_column});",
                    risk="medium",
                )
            ]
        table_name = sql_features.tables[0].table_name if sql_features.tables else "target_table"
        return [
            IndexRecommendation(
                ddl=f"CREATE INDEX idx_{table_name}_opt ON {table_name}(<where_col1>, <where_col2>);",
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


def _scenario_tags_for(sql_features: SqlFeatures) -> list[str]:
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


def _plan_symptom_tags_for(
    evidence: EvidenceEnvelope,
    findings: list[RuleFinding],
) -> list[str]:
    tags: set[str] = set()
    for finding in findings:
        tags.update(_plan_symptoms_from_finding(finding.code))
    if evidence.explain_json:
        tags.update(_collect_plan_terms(evidence.explain_json))
    return sorted(tags)


def _root_cause_tags_for(findings: list[RuleFinding]) -> list[str]:
    tags: set[str] = set()
    for finding in findings:
        tags.update(_root_causes_from_finding(finding.code))
    return sorted(tags)


def _plan_symptoms_from_finding(code: str) -> set[str]:
    normalized = _normalize_tag(code)
    mapping = {
        "limit_with_order_by": {"using_filesort"},
        "full_table_scan": {"all"},
        "temporary_table": {"using_temporary"},
    }
    return mapping.get(normalized, {normalized} if normalized else set())


def _root_causes_from_finding(code: str) -> set[str]:
    normalized = _normalize_tag(code)
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
            normalized_key = _normalize_tag(str(key))
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
            if normalized_key in {"access_type", "join_type", "node_type"} and isinstance(nested, str):
                terms.add(_normalize_tag(nested))
            terms.update(_collect_plan_terms(nested))
    elif isinstance(value, list):
        for item in value:
            terms.update(_collect_plan_terms(item))
    elif isinstance(value, str):
        normalized = _normalize_tag(value)
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


def _normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


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
