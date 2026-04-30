from chatdba.cases.pgvector_retriever import (
    PgVectorCaseRetriever,
    VectorSearchHit,
    build_case_embedding_text,
)
from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import CaseRetrievalQuery


class FakeEmbeddingGateway:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.texts: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.texts.append(text)
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [0.1, 0.2, 0.3]


def test_pgvector_case_retriever_merges_vector_hits_with_rule_candidates():
    seen = {}
    retriever = PgVectorCaseRetriever(
        cases=[
            OptimizationCase(
                case_id="generic-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by"],
                case_card="generic order by case",
                quality_score=0.9,
            ),
            OptimizationCase(
                case_id="vector-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                plan_symptom_tags=["using_filesort"],
                root_cause_tags=["missing_composite_index"],
                case_card="vector filesort case",
                quality_score=0.3,
            ),
        ],
        embedding_gateway=FakeEmbeddingGateway(),
        vector_search=lambda *, query, embedding, top_k: seen.setdefault(
            "hits",
            [
                VectorSearchHit(case_id="vector-case", vector_score=0.98),
            ],
        ),
        vector_top_k=6,
        candidate_limit=6,
    )

    result = retriever.retrieve(
        CaseRetrievalQuery(
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by", "limit"],
            plan_symptom_tags=["using_filesort"],
            root_cause_tags=["missing_composite_index"],
            embedding_text="mysql select order by limit using filesort",
        ),
        limit=3,
    )

    assert [case.case_id for case in result] == ["vector-case", "generic-case"]
    assert seen["hits"][0].vector_score == 0.98


def test_pgvector_case_retriever_falls_back_to_rule_only_when_embedding_fails():
    retriever = PgVectorCaseRetriever(
        cases=[
            OptimizationCase(
                case_id="rule-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by", "limit"],
                case_card="rule-only case",
                quality_score=0.8,
            )
        ],
        embedding_gateway=FakeEmbeddingGateway(fail=True),
        vector_search=lambda *, query, embedding, top_k: [
            VectorSearchHit(case_id="rule-case", vector_score=0.99)
        ],
    )

    result = retriever.retrieve(
        CaseRetrievalQuery(
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by", "limit"],
            embedding_text="mysql select order by limit",
        ),
        limit=3,
    )

    assert [case.case_id for case in result] == ["rule-case"]


def test_pgvector_case_retriever_does_not_vector_filter_on_predicate_profile_tags_only():
    seen = {}

    def vector_search(*, query, embedding, top_k):
        seen["scenario_tags"] = query.scenario_tags
        return [VectorSearchHit(case_id="legacy-case", vector_score=0.98)]

    retriever = PgVectorCaseRetriever(
        cases=[
            OptimizationCase(
                case_id="legacy-case",
                db_type="mysql",
                db_version_major="8.0",
                sql_type="select",
                scenario_tags=["order_by"],
                root_cause_tags=["implicit_cast"],
                case_card="legacy implicit cast case",
                quality_score=0.9,
            )
        ],
        embedding_gateway=FakeEmbeddingGateway(),
        vector_search=vector_search,
    )

    result = retriever.retrieve(
        CaseRetrievalQuery(
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["where_filter", "equality_predicate"],
            root_cause_tags=["implicit_cast"],
            embedding_text="mysql select where implicit cast",
        ),
        limit=3,
    )

    assert seen["scenario_tags"] == []
    assert [case.case_id for case in result] == ["legacy-case"]


def test_build_case_embedding_text_contains_retrieval_signal_fields():
    text = build_case_embedding_text(
        OptimizationCase(
            case_id="case-1",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by", "limit"],
            plan_symptom_tags=["using_filesort"],
            root_cause_tags=["missing_composite_index"],
            action_tags=["add_index", "sql_rewrite"],
            case_card="filesort fixed case",
            full_text="create index idx_orders_created_at on orders(created_at)",
        )
    )

    assert "mysql" in text
    assert "using_filesort" in text
    assert "missing_composite_index" in text
    assert "add_index" in text
