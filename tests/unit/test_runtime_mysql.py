from chatdba.db.runtime_mysql import (
    MysqlConnectionConfig,
    RuntimeMysqlClient,
    SourceMysqlConnectionFactory,
)


def test_connection_factory_builds_runtime_client_from_route():
    route = type(
        "Route",
        (),
        {
            "host": "10.0.0.10",
            "port": 3306,
            "default_schema": "shop",
            "credentials": {
                "username": "readonly",
                "password": "secret",
            },
        },
    )()

    factory = SourceMysqlConnectionFactory(
        connect_timeout_seconds=3,
        query_timeout_seconds=8,
    )

    config = factory.build_config(route)

    assert config.host == "10.0.0.10"
    assert config.port == 3306
    assert config.database == "shop"
    assert config.username == "readonly"
    assert config.password == "secret"


def test_runtime_mysql_client_query_all_returns_dict_rows():
    class FakeCursor:
        def __init__(self):
            self.executed = None

        def execute(self, sql, params=None):
            self.executed = (sql, params)

        def fetchall(self):
            return [{"instance_id": "mysql-order-ro"}]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    connection = FakeConnection()
    client = RuntimeMysqlClient(
        connection_factory=lambda **kwargs: connection,
        config=MysqlConnectionConfig(
            host="127.0.0.1",
            port=3306,
            username="readonly",
            password="secret",
            database="metadata",
            connect_timeout_seconds=3,
            query_timeout_seconds=8,
        ),
    )

    rows = client.query_all(
        "select * from db_instances where instance_id = %s",
        ["mysql-order-ro"],
    )

    assert rows == [{"instance_id": "mysql-order-ro"}]
    assert connection.cursor_obj.executed == (
        "select * from db_instances where instance_id = %s",
        ["mysql-order-ro"],
    )
    assert connection.closed is True
