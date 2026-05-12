from pydantic import BaseModel, Field


class FaultPlanStep(BaseModel):
    step_id: int
    agent: str
    date: str
    query_background: str
    query: str
    reason: str


class FaultDiagnosisProfile(BaseModel):
    input_text: str
    system_name: str | None = None
    management_ip: str | None = None
    business_ip: str | None = None
    primary_ip: str | None = None
    start_time: str
    end_time: str
    timezone: str = "Asia/Shanghai"
    query_background: str
    plan: list[FaultPlanStep] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class TopSqlRecord(BaseModel):
    database: str | None = None
    running_seconds: float | None = None
    sql_text: str


class TopSqlEvidence(BaseModel):
    agent_type: str = "top_sql"
    status: str
    rows: list[TopSqlRecord] = Field(default_factory=list)
    summary: str = ""
    error_message: str | None = None


class MetricPoint(BaseModel):
    timestamp: int
    value: float


class MetricSeries(BaseModel):
    metric_name: str
    ip: str
    unit: str | None = None
    values: list[MetricPoint] = Field(default_factory=list)


class MetricEvidence(BaseModel):
    agent_type: str = "metric"
    status: str
    metrics: list[MetricSeries] = Field(default_factory=list)
    summary: str = ""
    error_message: str | None = None


class FaultDiagnosisReport(BaseModel):
    task_id: str
    summary: str
    markdown: str
    root_cause: str
    recommendations: list[str] = Field(default_factory=list)
    profile: FaultDiagnosisProfile
    top_sql: TopSqlEvidence
    metrics: MetricEvidence
