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


def test_parse_sql_features_ignores_cte_names_as_physical_tables():
    features = parse_sql_features(
        "with recent_orders as (select * from orders) select * from recent_orders"
    )

    assert [table.table_name for table in features.tables] == ["orders"]
