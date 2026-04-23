from pydantic import BaseModel, Field


class OptimizationCase(BaseModel):
    case_id: str
    db_type: str
    scenario_tags: list[str] = Field(default_factory=list)
    case_card: str
    quality_score: float = 0.0
