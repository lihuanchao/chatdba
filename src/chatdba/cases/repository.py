from pydantic import BaseModel, Field


class OptimizationCase(BaseModel):
    case_id: str
    db_type: str
    db_version_major: str | None = None
    sql_type: str | None = None
    workload_type: str | None = None
    scenario_tags: list[str] = Field(default_factory=list)
    plan_symptom_tags: list[str] = Field(default_factory=list)
    root_cause_tags: list[str] = Field(default_factory=list)
    action_tags: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    tables_count_bucket: str | None = None
    estimated_rows_bucket: str | None = None
    case_card: str
    full_text: str | None = None
    keyword_score: float = 0.0
    vector_score: float = 0.0
    rerank_score: float = 0.0
    quality_score: float = 0.0
