from pydantic import BaseModel, Field


class Bottleneck(BaseModel):
    code: str
    evidence: str


class SqlRewrite(BaseModel):
    title: str
    sql: str


class IndexRecommendation(BaseModel):
    ddl: str
    risk: str


class Risk(BaseModel):
    level: str
    description: str


class SimilarCase(BaseModel):
    case_id: str
    reason: str


class OptimizationReport(BaseModel):
    task_id: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    bottlenecks: list[Bottleneck]
    sql_rewrites: list[SqlRewrite]
    index_recommendations: list[IndexRecommendation]
    risks: list[Risk]
    validation_steps: list[str]
    similar_cases: list[SimilarCase]

