from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute


class FakeRouter:
    def __init__(self, envelope):
        self.envelope = envelope

    def resolve(self, tables):
        return self.envelope


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
                collection_errors=["No metadata route found for one or more tables."],
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
    assert "No metadata route found" in evidence.collection_errors[0]
