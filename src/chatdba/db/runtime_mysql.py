from typing import Any

from pydantic import BaseModel


class MysqlConnectionConfig(BaseModel):
    host: str
    port: int
    username: str
    password: str
    database: str
    connect_timeout_seconds: int
    query_timeout_seconds: int


class RuntimeMysqlClient:
    def __init__(
        self,
        connection_factory,
        config: MysqlConnectionConfig,
        *,
        cursorclass: Any | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._config = config
        self._cursorclass = cursorclass

    def _connect(self):
        if self._connection_factory is None:
            raise RuntimeError("缺少 PyMySQL 依赖，无法创建 MySQL 运行时连接。")
        connect_kwargs = dict(
            host=self._config.host,
            port=self._config.port,
            user=self._config.username,
            password=self._config.password,
            database=self._config.database,
            connect_timeout=self._config.connect_timeout_seconds,
            read_timeout=self._config.query_timeout_seconds,
            write_timeout=self._config.query_timeout_seconds,
        )
        if self._cursorclass is not None:
            connect_kwargs["cursorclass"] = self._cursorclass
        return self._connection_factory(**connect_kwargs)

    def query_one(self, sql: str) -> dict[str, object]:
        rows = self.query_all(sql)
        if not rows:
            raise RuntimeError(f"MySQL query returned no rows: {sql}")
        return rows[0]

    def query_all(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> list[dict[str, object]]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()


class SourceMysqlConnectionFactory:
    def __init__(
        self,
        *,
        connect_timeout_seconds: int,
        query_timeout_seconds: int,
        connection_factory=None,
        cursorclass: Any | None = None,
    ) -> None:
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds
        self._connection_factory = connection_factory
        self._cursorclass = cursorclass

    def build_config(self, route) -> MysqlConnectionConfig:
        return MysqlConnectionConfig(
            host=route.host,
            port=route.port or 3306,
            username=route.credentials["username"],
            password=route.credentials["password"],
            database=route.default_schema or "mysql",
            connect_timeout_seconds=self._connect_timeout_seconds,
            query_timeout_seconds=self._query_timeout_seconds,
        )

    def create_client(self, route) -> RuntimeMysqlClient:
        return RuntimeMysqlClient(
            self._connection_factory,
            self.build_config(route),
            cursorclass=self._cursorclass,
        )


def build_metadata_client(settings) -> RuntimeMysqlClient:
    import pymysql

    return RuntimeMysqlClient(
        connection_factory=pymysql.connect,
        config=MysqlConnectionConfig(
            host=settings.metadata_mysql_host,
            port=settings.metadata_mysql_port,
            username=settings.metadata_mysql_user,
            password=settings.metadata_mysql_password,
            database=settings.metadata_mysql_database,
            connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
            query_timeout_seconds=settings.mysql_query_timeout_seconds,
        ),
        cursorclass=pymysql.cursors.DictCursor,
    )
