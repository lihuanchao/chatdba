import asyncio
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

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


def optimization_case_from_row(row: Mapping[str, Any]) -> OptimizationCase:
    plan_features = _as_mapping(row.get("plan_features"))
    optimization_actions = _as_sequence(row.get("optimization_actions"))
    return OptimizationCase(
        case_id=str(row["case_id"]),
        db_type=str(row["db_type"]),
        db_version_major=_major_version(row.get("db_version")),
        sql_type=_optional_str(row.get("sql_type") or plan_features.get("sql_type")),
        workload_type=_optional_str(
            row.get("workload_type") or plan_features.get("workload_type")
        ),
        scenario_tags=_string_list(row.get("scenario_tags")),
        plan_symptom_tags=_string_list(
            plan_features.get("plan_symptom_tags")
            or plan_features.get("plan_symptoms")
            or plan_features.get("symptoms")
        ),
        root_cause_tags=_string_list(row.get("root_cause_tags")),
        action_tags=_action_tags(optimization_actions, plan_features),
        risk_tags=_string_list(plan_features.get("risk_tags")),
        tables_count_bucket=_optional_str(plan_features.get("tables_count_bucket")),
        estimated_rows_bucket=_optional_str(plan_features.get("estimated_rows_bucket")),
        case_card=str(row["case_card"]),
        full_text=_optional_str(row.get("full_text")),
        keyword_score=_float_value(plan_features.get("keyword_score")),
        vector_score=_float_value(plan_features.get("vector_score")),
        rerank_score=_float_value(plan_features.get("rerank_score")),
        quality_score=_float_value(row.get("quality_score")),
    )


def load_optimization_cases(
    database_url: str,
    *,
    limit: int = 500,
) -> list[OptimizationCase]:
    if not database_url:
        return []
    return asyncio.run(_load_optimization_cases_async(database_url, limit=limit))


async def _load_optimization_cases_async(
    database_url: str,
    *,
    limit: int,
) -> list[OptimizationCase]:
    import asyncpg

    connection = await asyncpg.connect(_asyncpg_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT
                case_id,
                db_type,
                db_version,
                scenario_tags,
                plan_features,
                root_cause_tags,
                optimization_actions,
                case_card,
                full_text,
                quality_score
            FROM optimization_cases
            ORDER BY quality_score DESC, created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [optimization_case_from_row(dict(row)) for row in rows]
    finally:
        await connection.close()


def _asyncpg_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _major_version(value: object) -> str | None:
    text = _optional_str(value)
    if not text:
        return None
    parts = text.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return text


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item).strip()]
    return []


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> Sequence[Any]:
    if isinstance(value, str):
        return []
    return value if isinstance(value, Sequence) else []


def _action_tags(
    optimization_actions: Sequence[Any],
    plan_features: Mapping[str, Any],
) -> list[str]:
    tags = _string_list(plan_features.get("action_tags"))
    for action in optimization_actions:
        if isinstance(action, Mapping):
            action_type = action.get("type") or action.get("action") or action.get("tag")
            if action_type:
                tags.append(str(action_type))
        elif action:
            tags.append(str(action))
    return _dedupe(tags)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _float_value(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
