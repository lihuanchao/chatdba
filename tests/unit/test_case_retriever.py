from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases


def test_retrieve_cases_filters_by_db_type_and_tag():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="filesort fixed"),
        OptimizationCase(case_id="case-2", db_type="postgresql", scenario_tags=["order_by"], case_card="not mysql"),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=3)

    assert [case.case_id for case in result] == ["case-1"]
