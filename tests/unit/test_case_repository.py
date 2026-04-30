from chatdba.cases.repository import optimization_case_from_row


def test_optimization_case_from_row_maps_case_library_columns():
    case = optimization_case_from_row(
        {
            "case_id": "case-mysql8-order-list-filesort-001",
            "db_type": "mysql",
            "db_version": "8.0",
            "scenario_tags": ["join", "order_by", "limit"],
            "plan_features": {
                "plan_symptom_tags": ["all", "using_filesort"],
                "estimated_rows_bucket": "10m+",
                "keyword_score": 0.91,
                "vector_score": 0.82,
                "rerank_score": 0.95,
            },
            "root_cause_tags": ["missing_composite_index"],
            "optimization_actions": [
                {"type": "add_index"},
                {"type": "sql_rewrite"},
            ],
            "case_card": "MySQL 8.0 filesort case",
            "full_text": "full case detail",
            "quality_score": 0.9,
        }
    )

    assert case.case_id == "case-mysql8-order-list-filesort-001"
    assert case.db_version_major == "8.0"
    assert case.scenario_tags == ["join", "order_by", "limit"]
    assert case.plan_symptom_tags == ["all", "using_filesort"]
    assert case.root_cause_tags == ["missing_composite_index"]
    assert case.action_tags == ["add_index", "sql_rewrite"]
    assert case.estimated_rows_bucket == "10m+"
    assert case.keyword_score == 0.91
    assert case.vector_score == 0.82
    assert case.rerank_score == 0.95
    assert case.quality_score == 0.9
