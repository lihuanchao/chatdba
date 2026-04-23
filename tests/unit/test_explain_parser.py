from chatdba.explain.mysql_json import extract_plan_features


def test_extract_plan_features_detects_full_scan():
    explain = {"query_block": {"table": {"table_name": "orders", "access_type": "ALL", "rows_examined_per_scan": 120000}}}

    features = extract_plan_features(explain)

    assert features[0].code == "full_table_scan"
    assert features[0].severity == "high"


def test_extract_plan_features_ignores_join_output_cardinality_for_full_scan():
    explain = {
        "query_block": {
            "table": {
                "table_name": "orders",
                "access_type": "ALL",
                "rows_examined_per_scan": 0,
                "rows_produced_per_join": 200000,
            }
        }
    }

    features = extract_plan_features(explain)

    assert features == []
