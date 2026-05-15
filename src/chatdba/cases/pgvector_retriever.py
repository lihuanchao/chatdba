import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import (
    CaseRetrievalQuery,
    RetrievalScoreOverride,
    hard_filter_scenario_tags,
    retrieve_cases_for_query,
)

LOGGER = logging.getLogger(__name__)


class TextEmbeddingGateway(Protocol):
    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError


@dataclass(frozen=True)
class VectorSearchHit:
    case_id: str
    vector_score: float


class PgVectorCaseRetriever:
    def __init__(
        self,
        *,
        cases: list[OptimizationCase],
        embedding_gateway: TextEmbeddingGateway,
        database_url: str | None = None,
        vector_search: Callable[..., list[VectorSearchHit]] | None = None,
        vector_top_k: int = 12,
        candidate_limit: int = 12,
    ) -> None:
        self._cases = cases
        self._embedding_gateway = embedding_gateway
        self._database_url = database_url or ""
        self._vector_search = vector_search
        self._vector_top_k = max(1, vector_top_k)
        self._candidate_limit = max(1, candidate_limit)

    def retrieve(
        self,
        query: CaseRetrievalQuery,
        *,
        limit: int,
    ) -> list[OptimizationCase]:
        rule_candidates = retrieve_cases_for_query(
            self._cases,
            query,
            limit=max(limit, self._candidate_limit),
        )
        vector_hits = self._vector_hits_for(query)
        if not vector_hits:
            return rule_candidates[:limit]

        case_index = {case.case_id: case for case in self._cases}
        candidate_map = {case.case_id: case for case in rule_candidates}
        score_overrides: dict[str, RetrievalScoreOverride] = {}
        for hit in vector_hits:
            case = case_index.get(hit.case_id)
            if case is None:
                continue
            candidate_map.setdefault(case.case_id, case)
            score_overrides[case.case_id] = RetrievalScoreOverride(
                vector_score=hit.vector_score
            )

        return retrieve_cases_for_query(
            list(candidate_map.values()),
            query,
            limit=limit,
            score_overrides=score_overrides,
        )

    def _vector_hits_for(self, query: CaseRetrievalQuery) -> list[VectorSearchHit]:
        embedding_text = (query.embedding_text or "").strip()
        if not embedding_text:
            return []

        if not self._database_url and self._vector_search is None:
            return []

        try:
            with _usage_operation(
                self._embedding_gateway,
                "case_embedding_retrieval",
            ):
                embedding = self._embedding_gateway.embed_text(embedding_text)
        except Exception:
            LOGGER.warning("Case embedding generation failed, fallback to rule-only retrieval.", exc_info=True)
            return []

        vector_query = replace(
            query,
            scenario_tags=hard_filter_scenario_tags(query.scenario_tags),
        )

        try:
            if self._vector_search is not None:
                return list(
                    self._vector_search(
                        query=vector_query,
                        embedding=embedding,
                        top_k=self._vector_top_k,
                    )
                )
            return asyncio.run(
                _search_vector_hits_async(
                    database_url=self._database_url,
                    query=vector_query,
                    embedding=embedding,
                    top_k=self._vector_top_k,
                )
            )
        except Exception:
            LOGGER.warning("pgvector case retrieval failed, fallback to rule-only retrieval.", exc_info=True)
            return []


def build_case_embedding_text(case: OptimizationCase) -> str:
    parts = [
        case.db_type,
        case.db_version_major or "",
        case.sql_type or "",
        " ".join(case.scenario_tags),
        " ".join(case.plan_symptom_tags),
        " ".join(case.root_cause_tags),
        " ".join(case.action_tags),
        case.case_card,
        case.full_text or "",
    ]
    return " ".join(part for part in parts if part).strip()


async def _search_vector_hits_async(
    *,
    database_url: str,
    query: CaseRetrievalQuery,
    embedding: list[float],
    top_k: int,
) -> list[VectorSearchHit]:
    import asyncpg

    connection = await asyncpg.connect(_asyncpg_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT
                case_id,
                embedding <=> CAST($1 AS vector) AS distance
            FROM optimization_cases
            WHERE lower(db_type) = lower($2)
              AND embedding IS NOT NULL
              AND ($3::text IS NULL OR db_version LIKE ($3 || '%'))
              AND (
                $4::text IS NULL
                OR lower(COALESCE(plan_features->>'sql_type', '')) = lower($4)
              )
              AND (
                cardinality($5::text[]) = 0
                OR scenario_tags && $5::text[]
              )
            ORDER BY embedding <=> CAST($1 AS vector)
            LIMIT $6
            """,
            vector_literal(embedding),
            query.db_type,
            query.db_version_major,
            query.sql_type,
            query.scenario_tags,
            top_k,
        )
    finally:
        await connection.close()

    hits: list[VectorSearchHit] = []
    for row in rows:
        distance = float(row["distance"])
        hits.append(
            VectorSearchHit(
                case_id=str(row["case_id"]),
                vector_score=_distance_to_score(distance),
            )
        )
    return hits


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.10f}" for value in values) + "]"


def _distance_to_score(distance: float) -> float:
    if distance <= 0:
        return 1.0
    return 1.0 / (1.0 + distance)


def _asyncpg_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _usage_operation(embedding_gateway: TextEmbeddingGateway, operation: str):
    usage_operation = getattr(embedding_gateway, "usage_operation", None)
    if callable(usage_operation):
        return usage_operation(operation)
    return _NoopUsageOperation()


class _NoopUsageOperation:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
