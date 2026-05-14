from chatdba.sql.parser import parse_sql_features
from chatdba.sql.schema_qualification import (
    extract_schema_name_reply,
    qualify_unqualified_tables,
    split_schema_prefixed_sql,
    unqualified_table_names,
)


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


def test_parse_sql_features_preserves_schema_qualified_table_names():
    features = parse_sql_features("select * from shop.orders")

    assert features.tables[0].schema_name == "shop"
    assert features.tables[0].table_name == "orders"


def test_qualify_unqualified_tables_adds_schema_to_requested_tables_only():
    sql = (
        "select * from orders o join shop.users u on o.user_id = u.id "
        "join products p on p.id = o.product_id"
    )

    qualified = qualify_unqualified_tables(
        sql,
        schema_name="crm",
        table_names=["orders"],
    )

    assert qualified == (
        "SELECT * FROM crm.orders AS o JOIN shop.users AS u ON o.user_id = u.id "
        "JOIN products AS p ON p.id = o.product_id"
    )


def test_unqualified_table_names_returns_all_join_tables_without_schema():
    names = unqualified_table_names(
        "select * from orders o join users u on o.user_id = u.id "
        "join shop.products p on p.id = o.product_id"
    )

    assert names == ["orders", "users"]


def test_extract_schema_name_reply_allows_hyphenated_schema_names():
    assert extract_schema_name_reply("international-base") == "international-base"
    assert extract_schema_name_reply("库名: international-base") == "international-base"
    assert extract_schema_name_reply("`international-base`") == "international-base"


def test_split_schema_prefixed_sql_extracts_database_name_before_select():
    schema_name, sql = split_schema_prefixed_sql(
        "zqsoft_mom_wms_istorage_lw  SELECT count(*) FROM wmsoutputdetail"
    )

    assert schema_name == "zqsoft_mom_wms_istorage_lw"
    assert sql == "SELECT count(*) FROM wmsoutputdetail"


def test_split_schema_prefixed_sql_allows_quoted_hyphenated_database_name():
    schema_name, sql = split_schema_prefixed_sql(
        "`international-base` SELECT * FROM sys_file_info"
    )

    assert schema_name == "international-base"
    assert sql == "SELECT * FROM sys_file_info"


def test_split_schema_prefixed_sql_preserves_plain_select():
    schema_name, sql = split_schema_prefixed_sql("SELECT * FROM orders")

    assert schema_name is None
    assert sql == "SELECT * FROM orders"


def test_qualify_unqualified_tables_quotes_hyphenated_schema_names():
    qualified = qualify_unqualified_tables(
        "select * from orders",
        schema_name="international-base",
        table_names=["orders"],
    )

    assert qualified == "SELECT * FROM `international-base`.orders"
