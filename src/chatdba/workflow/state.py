from typing import TypedDict

from chatdba.db.mysql_collector import MysqlEvidence
from chatdba.domain.models import RuleFinding, SqlFeatures


class SqlOptimizationState(TypedDict, total=False):
    task_id: str
    raw_sql: str
    default_schema: str
    sql_features: SqlFeatures
    evidence: MysqlEvidence
    findings: list[RuleFinding]
