from chatdba.db.mysql_collector import MysqlEvidenceCollector, MysqlTableTarget


class FakeMysqlClient:
    def __init__(self):
        self.queries = []

    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {"EXPLAIN": "{\"query_block\":{\"table\":{\"table_name\":\"orders\",\"access_type\":\"ALL\"}}}"}
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


class BytesExplainMysqlClient(FakeMysqlClient):
    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {
                "EXPLAIN": b'{"query_block":{"table":{"table_name":"orders","access_type":"ALL"}}}'
            }
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


class RecordingMysqlClient(FakeMysqlClient):
    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {"EXPLAIN": {"query_block": {"table": {"table_name": "orders", "access_type": "ALL"}}}}
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


def test_collector_uses_explain_format_json_and_show_create_table():
    collector = MysqlEvidenceCollector(FakeMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    evidence = collector.collect("select * from shop.orders", [target])

    assert evidence.explain_json["query_block"]["table"]["access_type"] == "ALL"
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")


def test_collector_parses_bytes_explain_payload():
    collector = MysqlEvidenceCollector(BytesExplainMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    evidence = collector.collect("select * from shop.orders", [target])

    assert evidence.explain_json["query_block"]["table"]["table_name"] == "orders"


def test_collector_escapes_backticks_in_show_create_table_query():
    client = RecordingMysqlClient()
    collector = MysqlEvidenceCollector(client)
    target = MysqlTableTarget(schema_name="sh`op", table_name="or`ders")

    collector.collect("select * from `sh`op`.`or`ders`", [target])

    assert client.queries[1] == "SHOW CREATE TABLE `sh``op`.`or``ders`"
