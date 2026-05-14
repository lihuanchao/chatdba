from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute


class FakeRouter:
    def __init__(self, envelope):
        self.envelope = envelope

    def resolve(self, tables):
        return self.envelope

    def resolve_with_tables(self, tables):
        return self.envelope, tables


class FakeConnectionFactory:
    def __init__(self, client):
        self.client = client

    def create_client(self, route):
        return self.client


class SuccessfulMysqlClient:
    def query_one(self, sql: str):
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {
                "EXPLAIN": {
                    "query_block": {
                        "table": {
                            "table_name": "orders",
                            "access_type": "ALL",
                        }
                    }
                }
            }
        return {
            "Create Table": "CREATE TABLE orders (id bigint primary key)"
        }


class ExplainFailingMysqlClient(SuccessfulMysqlClient):
    def query_one(self, sql: str):
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            raise RuntimeError("explain timeout")
        return super().query_one(sql)


def make_full_route_envelope():
    return EvidenceEnvelope(
        status=EvidenceStatus.FULL,
        route=SourceRoute(
            instance_id="mysql-order-ro",
            db_type="mysql",
            version="8.0",
            host="10.0.0.10",
            port=3306,
            default_schema="shop",
            credentials={"username": "readonly", "password": "secret"},
            schema_names=["shop"],
        ),
    )


def test_routed_collector_returns_full_evidence_when_source_collection_succeeds():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.FULL
    assert evidence.explain_json["query_block"]["table"]["table_name"] == "orders"
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")
    assert evidence.collection_errors == []


def test_routed_collector_returns_partial_when_explain_fails_but_ddl_succeeds():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(ExplainFailingMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.PARTIAL
    assert evidence.explain_json is None
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")
    assert evidence.missing_evidence == ["explain_json"]
    assert "explain timeout" in evidence.collection_errors[0]


def test_routed_collector_preserves_sql_only_when_router_cannot_route():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(
            EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["元数据库未找到一个或多个表的路由信息。"],
            )
        ),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.SQL_ONLY
    assert evidence.route is None
    assert "未找到一个或多个表的路由信息" in evidence.collection_errors[0]


def test_routed_collector_preserves_sql_only_when_unqualified_table_is_ambiguous():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(
            EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["以下表名在元数据库中存在重复，请补充库名后重试：orders"],
            )
        ),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name=None, table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.SQL_ONLY
    assert evidence.route is None
    assert "请补充库名" in evidence.collection_errors[0]


def test_routed_collector_uses_router_resolved_tables_for_ddl_lookup():
    class ResolvedTableRouter(FakeRouter):
        def resolve_with_tables(self, tables):
            return self.envelope, [
                MysqlTableTarget(schema_name="shop", table_name="orders")
            ]

    collector = RoutedMysqlEvidenceCollector(
        router=ResolvedTableRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name=None, table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.FULL
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")


def test_routed_collector_uses_metadata_table_name_case_after_case_insensitive_route():
    class ResolvedTableRouter(FakeRouter):
        def resolve_with_tables(self, tables):
            return self.envelope, [
                MysqlTableTarget(schema_name="wms", table_name="sygcangkuinfo")
            ]

    class RecordingMysqlClient(SuccessfulMysqlClient):
        def __init__(self):
            self.queries = []

        def query_one(self, sql: str):
            self.queries.append(sql)
            if sql.startswith("SHOW CREATE TABLE"):
                return {"Create Table": "CREATE TABLE sygcangkuinfo (id bigint)"}
            return super().query_one(sql)

    client = RecordingMysqlClient()
    collector = RoutedMysqlEvidenceCollector(
        router=ResolvedTableRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(client),
    )

    evidence = collector.collect(
        "select * from WMS.SygCangKuInfo",
        [MysqlTableTarget(schema_name="WMS", table_name="SygCangKuInfo")],
    )

    assert evidence.status == EvidenceStatus.FULL
    assert "wms.sygcangkuinfo" in evidence.create_tables
    assert "SHOW CREATE TABLE `wms`.`sygcangkuinfo`" in client.queries
