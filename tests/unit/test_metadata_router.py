from chatdba.db.metadata_router import (
    MetadataRouteRow,
    MetadataRouter,
    MysqlMetadataRouteRepository,
)
from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import EvidenceStatus


class FakeMetadataRouteRepository:
    def __init__(self, rows):
        self.rows = rows
        self.requested = []

    def find_routes(self, tables):
        self.requested.append([(table.schema_name, table.table_name) for table in tables])
        return self.rows


class FakeMetadataMysqlClient:
    def __init__(self):
        self.sql = None
        self.params = None

    def query_all(self, sql, params):
        self.sql = sql
        self.params = params
        return [
            {
                "schema_name": "shop",
                "table_name": "orders",
                "instance_id": "mysql-order-ro",
                "host": "10.0.0.10",
                "port": 3306,
                "readonly_username": "readonly",
                "readonly_password": "secret",
                "default_schema": "shop",
                "db_type": "mysql",
                "version": "8.0",
                "enabled": 1,
            }
        ]


def test_metadata_route_repository_maps_rows_from_metadata_database():
    client = FakeMetadataMysqlClient()
    repository = MysqlMetadataRouteRepository(
        client=client,
        route_table="table_routes",
        instance_table="db_instances",
    )

    rows = repository.find_routes(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert rows[0].instance_id == "mysql-order-ro"
    assert rows[0].readonly_username == "readonly"
    assert "table_routes" in client.sql
    assert client.params == ["shop", "orders"]


def test_router_returns_single_instance_route():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            )
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.FULL
    assert route.route.instance_id == "mysql-order-ro"
    assert route.route.credentials == {"username": "readonly", "password": "secret"}
    assert route.collection_errors == []


def test_router_degrades_when_tables_span_multiple_instances():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
            MetadataRouteRow(
                schema_name="crm",
                table_name="customer",
                instance_id="mysql-crm-ro",
                host="10.0.0.20",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="crm",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [
            MysqlTableTarget(schema_name="shop", table_name="orders"),
            MysqlTableTarget(schema_name="crm", table_name="customer"),
        ]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert route.missing_evidence == ["route_info", "explain_json", "create_table"]
    assert "multiple source instances" in route.collection_errors[0]


def test_router_degrades_when_table_route_is_missing():
    repository = FakeMetadataRouteRepository([])
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "No metadata route found" in route.collection_errors[0]
