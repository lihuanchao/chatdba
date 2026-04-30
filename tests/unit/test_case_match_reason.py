from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import CaseRetrievalQuery
from chatdba.workflow.case_match_reason import similar_cases_for_report


def test_similar_cases_for_report_explains_tag_and_version_hits():
    cases = [
        OptimizationCase(
            case_id="case-filesort-1",
            db_type="mysql",
            db_version_major="8.0",
            sql_type="select",
            scenario_tags=["order_by", "limit"],
            plan_symptom_tags=["using_filesort"],
            root_cause_tags=["missing_composite_index"],
            case_card="ORDER BY LIMIT 缺少联合索引案例",
        )
    ]
    query = CaseRetrievalQuery(
        db_type="mysql",
        db_version_major="8.0",
        sql_type="select",
        scenario_tags=["order_by", "limit"],
        plan_symptom_tags=["using_filesort"],
        root_cause_tags=["missing_composite_index"],
    )

    similar_cases = similar_cases_for_report(cases, query)

    assert similar_cases[0].case_id == "case-filesort-1"
    assert "根因标签命中：missing_composite_index" in similar_cases[0].reason
    assert "执行计划症状命中：using_filesort" in similar_cases[0].reason
    assert "SQL场景命中：order_by, limit" in similar_cases[0].reason
    assert "数据库版本匹配：8.0" in similar_cases[0].reason
