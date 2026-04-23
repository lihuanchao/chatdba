from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases


def test_retrieve_cases_filters_by_db_type_and_tag():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="filesort fixed"),
        OptimizationCase(case_id="case-2", db_type="postgresql", scenario_tags=["order_by"], case_card="not mysql"),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=3)

    assert [case.case_id for case in result] == ["case-1"]


def test_retrieve_cases_ranks_by_quality_score_descending():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="lower score", quality_score=0.4),
        OptimizationCase(case_id="case-2", db_type="mysql", scenario_tags=["order_by"], case_card="higher score", quality_score=0.9),
        OptimizationCase(case_id="case-3", db_type="mysql", scenario_tags=["join"], case_card="other tag", quality_score=1.0),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=3)

    assert [case.case_id for case in result] == ["case-2", "case-1"]


def test_retrieve_cases_applies_limit_after_ranking():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="lower score", quality_score=0.4),
        OptimizationCase(case_id="case-2", db_type="mysql", scenario_tags=["order_by"], case_card="higher score", quality_score=0.9),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=1)

    assert [case.case_id for case in result] == ["case-2"]
