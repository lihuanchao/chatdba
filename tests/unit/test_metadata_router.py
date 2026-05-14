from chatdba.db.metadata_router import (
    MetadataRouteRow,
    MetadataRouter,
    MysqlMetadataRouteRepository,
)
from chatdba.db.metadata_repository import StaticMetadataRepository
from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import TableReference
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


def test_metadata_route_repository_queries_unqualified_table_by_name_only():
    client = FakeMetadataMysqlClient()
    repository = MysqlMetadataRouteRepository(
        client=client,
        route_table="table_routes",
        instance_table="db_instances",
    )

    repository.find_routes(
        [MysqlTableTarget(schema_name=None, table_name="orders")]
    )

    assert "r.table_name = %s" in client.sql
    assert "r.schema_name = %s" not in client.sql
    assert client.params == ["orders"]


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


def test_router_asks_for_schema_when_unqualified_single_table_is_ambiguous():
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
                schema_name="archive",
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
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name=None, table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "orders" in route.collection_errors[0]
    assert "请补充库名" in route.collection_errors[0]


def test_router_asks_for_schema_when_unqualified_table_exists_on_multiple_instances():
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
                schema_name="archive",
                table_name="orders",
                instance_id="mysql-archive-ro",
                host="10.0.0.20",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="archive",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name=None, table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "orders" in route.collection_errors[0]
    assert "请补充库名" in route.collection_errors[0]


def test_router_asks_for_schema_when_same_schema_table_exists_on_multiple_instances():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-a",
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
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-b",
                host="10.0.0.20",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name=None, table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "orders" in route.collection_errors[0]
    assert "请补充库名" in route.collection_errors[0]


def test_router_resolves_schema_qualified_table_when_same_table_name_exists_elsewhere():
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
                schema_name="archive",
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
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.FULL
    assert route.route.instance_id == "mysql-order-ro"
    assert route.route.default_schema == "shop"


def test_router_asks_for_schema_before_inferring_common_schema_for_duplicate_table():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-main-ro",
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
                schema_name="shop",
                table_name="users",
                instance_id="mysql-main-ro",
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
                schema_name="archive",
                table_name="orders",
                instance_id="mysql-main-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [
            MysqlTableTarget(schema_name=None, table_name="orders"),
            MysqlTableTarget(schema_name=None, table_name="users"),
        ]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "orders" in route.collection_errors[0]
    assert "请补充库名" in route.collection_errors[0]


def test_router_degrades_when_unqualified_tables_have_no_common_schema():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-main-ro",
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
                table_name="users",
                instance_id="mysql-main-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [
            MysqlTableTarget(schema_name=None, table_name="orders"),
            MysqlTableTarget(schema_name=None, table_name="users"),
        ]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "请补充库名" in route.collection_errors[0]
    assert "orders" in route.collection_errors[0]
    assert "users" in route.collection_errors[0]


def test_router_asks_for_schema_when_join_has_missing_unqualified_table_route():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="wms",
                table_name="wmsoutputdetail",
                instance_id="mysql-wms-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="wms",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
            MetadataRouteRow(
                schema_name="wms",
                table_name="wmsoutputmain",
                instance_id="mysql-wms-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="wms",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [
            MysqlTableTarget(schema_name=None, table_name="wmsoutputdetail"),
            MysqlTableTarget(schema_name=None, table_name="wmsoutputmain"),
            MysqlTableTarget(schema_name=None, table_name="wmssortingdetail"),
        ]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "请补充库名" in route.collection_errors[0]
    assert "wmsoutputdetail" in route.collection_errors[0]
    assert "wmsoutputmain" in route.collection_errors[0]
    assert "wmssortingdetail" in route.collection_errors[0]


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
    assert "涉及多个源实例" in route.collection_errors[0]


def test_router_degrades_when_table_route_is_missing():
    repository = FakeMetadataRouteRepository([])
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "未找到一个或多个表的路由信息" in route.collection_errors[0]


def test_static_metadata_repository_preserves_unqualified_tables():
    repository = StaticMetadataRepository(default_schema="shop")

    targets = repository.resolve_tables(
        [TableReference(schema_name=None, table_name="orders")]
    )

    assert targets[0].schema_name is None
    assert targets[0].table_name == "orders"
