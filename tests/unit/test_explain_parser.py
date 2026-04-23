from chatdba.explain.mysql_json import extract_plan_features


def test_extract_plan_features_detects_full_scan():
    explain = {"query_block": {"table": {"table_name": "orders", "access_type": "ALL", "rows_examined_per_scan": 120000}}}

    features = extract_plan_features(explain)

    assert features[0].code == "full_table_scan"
    assert features[0].severity == "high"
