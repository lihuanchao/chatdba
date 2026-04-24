import json
from typing import Protocol

from chatdba.cases.repository import OptimizationCase
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
        system_prompt = (
            "你是资深 MySQL DBA，请根据结构化证据输出 SQL优化报告。"
            "必须返回合法 JSON，并包含置信度、证据状态、瓶颈、SQL 改写、索引建议、风险和验证步骤。"
        )
        similar_cases = self._select_cases(sql_features)
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
            for case in self._select_cases(sql_features)
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

    def _select_cases(self, sql_features: SqlFeatures) -> list[OptimizationCase]:
        matched: list[OptimizationCase] = []
        tags: set[str] = set()
        if sql_features.order_by:
            tags.add("order_by")
        if sql_features.group_by:
            tags.add("group_by")
        if sql_features.joins:
            tags.add("join")

        for case in sorted(self._cases, key=lambda item: item.quality_score, reverse=True):
            if not case.scenario_tags or tags.intersection(case.scenario_tags):
                matched.append(case)
        return matched[:3]

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
            limitations.insert(0, "No source execution evidence was available.")
        elif evidence.status == EvidenceStatus.PARTIAL:
            limitations.insert(0, "Only partial source evidence was available.")
        return limitations

    def _build_summary(
        self,
        findings: list[RuleFinding],
        status: EvidenceStatus,
    ) -> str:
        if findings:
            return findings[0].message
        if status == EvidenceStatus.SQL_ONLY:
            return "The report is based on SQL text, rules, and historical cases."
        if status == EvidenceStatus.PARTIAL:
            return "The report is based on partial source evidence plus SQL rules."
        return "The report is based on source execution evidence and SQL rules."

    def _build_sql_rewrites(
        self,
        raw_sql: str,
        findings: list[RuleFinding],
    ) -> list[SqlRewrite]:
        if any(finding.code == "limit_with_order_by" for finding in findings):
            return [
                SqlRewrite(
                    title="Review projection and predicate selectivity",
                    sql=raw_sql,
                )
            ]
        return []

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
                    ddl=f"create index idx_{table_name}_{order_column.lower()} on {table_name}({order_column})",
                    risk="medium",
                )
            ]
        return []

    def _build_risks(self, status: EvidenceStatus) -> list[Risk]:
        if status == EvidenceStatus.SQL_ONLY:
            return [
                Risk(
                    level="medium",
                    description="Recommendations are inferred without source execution evidence.",
                )
            ]
        return []

    def _build_validation_steps(self, status: EvidenceStatus) -> list[str]:
        if status == EvidenceStatus.FULL:
            return [
                "Run EXPLAIN FORMAT=JSON on the rewritten SQL and compare access paths.",
            ]
        if status == EvidenceStatus.PARTIAL:
            return [
                "Re-run the missing evidence collection on the target database before applying changes.",
            ]
        return [
            "Validate the SQL against the target source database before applying any recommendation.",
        ]
