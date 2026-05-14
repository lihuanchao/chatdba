from typing import TypedDict

from chatdba.domain.models import EvidenceEnvelope, RuleFinding, SqlFeatures
from chatdba.domain.report_schema import OptimizationReport


class SqlOptimizationState(TypedDict, total=False):
    task_id: str
    raw_sql: str
    schema_name: str | None
    default_schema: str
    sql_features: SqlFeatures
    evidence: EvidenceEnvelope
    findings: list[RuleFinding]
    report: OptimizationReport
