from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases


def test_retrieve_cases_filters_by_db_type_and_tag():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="filesort fixed"),
        OptimizationCase(case_id="case-2", db_type="postgresql", scenario_tags=["order_by"], case_card="not mysql"),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=3)

    assert [case.case_id for case in result] == ["case-1"]


def test_retrieve_cases_does_not_hard_filter_on_predicate_profile_tags_only():
    cases = [
        OptimizationCase(
            case_id="legacy-case",
            db_type="mysql",
            sql_type="select",
            scenario_tags=["order_by"],
            root_cause_tags=["implicit_cast"],
            case_card="legacy implicit cast case",
            quality_score=0.9,
        )
    ]

    result = retrieve_cases(
        cases,
        db_type="mysql",
        sql_type="select",
        scenario_tags=["where_filter", "equality_predicate"],
        root_cause_tags=["implicit_cast"],
        limit=3,
    )

    assert [case.case_id for case in result] == ["legacy-case"]


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


def test_retrieve_cases_applies_environment_hard_filters():
    cases = [
        OptimizationCase(
            case_id="case-1",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by"],
            case_card="matching mysql 8 select",
        ),
        OptimizationCase(
            case_id="case-2",
            db_type="mysql",
            db_version_major="5.7",
            sql_type="select",
            scenario_tags=["order_by"],
            case_card="wrong version",
        ),
        OptimizationCase(
            case_id="case-3",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="update",
            scenario_tags=["order_by"],
            case_card="wrong sql type",
        ),
    ]

    result = retrieve_cases(
        cases,
        db_type="mysql",
        db_version_major="8.0",
        sql_type="select",
        scenario_tags=["order_by"],
        limit=5,
    )

    assert [case.case_id for case in result] == ["case-1"]


def test_retrieve_cases_prioritizes_plan_symptoms_and_root_cause_over_quality_only():
    cases = [
        OptimizationCase(
            case_id="case-1",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by"],
            case_card="high quality but generic",
            quality_score=1.0,
        ),
        OptimizationCase(
            case_id="case-2",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by"],
            plan_symptom_tags=["using_filesort", "using_temporary"],
            root_cause_tags=["missing_composite_index"],
            case_card="lower quality but exact symptom and root cause",
            quality_score=0.2,
        ),
    ]

    result = retrieve_cases(
        cases,
        db_type="mysql",
        db_version_major="8.0",
        sql_type="select",
        scenario_tags=["order_by", "limit"],
        plan_symptom_tags=["using_filesort"],
        root_cause_tags=["missing_composite_index"],
        limit=5,
    )

    assert [case.case_id for case in result] == ["case-2", "case-1"]


def test_retrieve_cases_limits_duplicate_root_cause_templates():
    cases = [
        OptimizationCase(
            case_id=f"case-{index}",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by"],
            root_cause_tags=["missing_composite_index"],
            case_card=f"duplicate root cause {index}",
            quality_score=1.0 - index * 0.1,
        )
        for index in range(1, 5)
    ]

    result = retrieve_cases(
        cases,
        db_type="mysql",
        db_version_major="8.0",
        sql_type="select",
        scenario_tags=["order_by"],
        root_cause_tags=["missing_composite_index"],
        limit=5,
        max_same_root_cause=2,
    )

    assert [case.case_id for case in result] == ["case-1", "case-2"]
