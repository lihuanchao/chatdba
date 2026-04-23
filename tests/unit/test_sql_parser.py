from chatdba.sql.parser import parse_sql_features


def test_parse_sql_features_extracts_tables_and_limit():
    features = parse_sql_features(
        "select o.id, u.name from orders o join users u on o.user_id = u.id "
        "where o.status = 'PAID' order by o.created_at desc limit 20"
    )

    assert features.statement_type == "select"
    assert features.has_limit is True
    assert [table.table_name for table in features.tables] == ["orders", "users"]
    assert features.order_by == ["o.created_at DESC"]
