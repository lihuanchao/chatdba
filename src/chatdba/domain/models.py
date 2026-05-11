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


class EvidenceStatus(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    SQL_ONLY = "sql_only"


class ConfidenceLabel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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


class AgentTokenUsage(BaseModel):
    task_id: str
    provider: str = "qwen"
    model: str
    operation: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw_usage: dict[str, Any] = Field(default_factory=dict)


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


class SourceRoute(BaseModel):
    instance_id: str
    db_type: str
    version: str | None = None
    host: str | None = None
    port: int | None = None
    default_schema: str | None = None
    credentials: dict[str, str] = Field(default_factory=dict)
    schema_names: list[str] = Field(default_factory=list)


class EvidenceEnvelope(BaseModel):
    status: EvidenceStatus
    route: SourceRoute | None = None
    explain_json: dict[str, object] | None = None
    create_tables: dict[str, str] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    collection_errors: list[str] = Field(default_factory=list)
