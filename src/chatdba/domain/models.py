from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    RECEIVED = "received"
    PARSING_SQL = "parsing_sql"
    RESOLVING_DATABASE = "resolving_database"
    COLLECTING_METADATA = "collecting_metadata"
    COLLECTING_EXPLAIN = "collecting_explain"
    DIAGNOSING = "diagnosing"
    RETRIEVING_CASES = "retrieving_cases"
    GENERATING_REPORT = "generating_report"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class DingTalkContext(BaseModel):
    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: str | None = None
    session_webhook: str | None = None


class SqlOptimizationRequest(BaseModel):
    task_id: str
    raw_sql: str
    dingtalk: DingTalkContext | None = None


class TableReference(BaseModel):
    schema_name: str | None = None
    table_name: str
    alias: str | None = None


class SqlFeatures(BaseModel):
    fingerprint: str
    statement_type: str
    tables: list[TableReference] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    has_limit: bool = False


class PlanFeature(BaseModel):
    code: str
    severity: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RuleFinding(BaseModel):
    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)

