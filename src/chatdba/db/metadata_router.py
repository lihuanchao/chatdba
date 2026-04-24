from typing import Protocol

from pydantic import BaseModel

from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute


class MetadataRouteRow(BaseModel):
    schema_name: str
    table_name: str
    instance_id: str
    host: str
    port: int
    readonly_username: str
    readonly_password: str
    default_schema: str | None = None
    db_type: str = "mysql"
    version: str | None = None
    enabled: bool = True


class MetadataMysqlClient(Protocol):
    def query_all(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> list[dict[str, object]]:
        raise NotImplementedError


class MetadataRouteRepository(Protocol):
    def find_routes(
        self,
        tables: list[MysqlTableTarget],
    ) -> list[MetadataRouteRow]:
        raise NotImplementedError


class MysqlMetadataRouteRepository:
    def __init__(
        self,
        *,
        client: MetadataMysqlClient,
        route_table: str,
        instance_table: str,
    ) -> None:
        self._client = client
        self._route_table = route_table
        self._instance_table = instance_table

    def find_routes(
        self,
        tables: list[MysqlTableTarget],
    ) -> list[MetadataRouteRow]:
        if not tables:
            return []

        predicates: list[str] = []
        params: list[object] = []
        for table in tables:
            predicates.append("(r.schema_name = %s AND r.table_name = %s)")
            params.extend([table.schema_name, table.table_name])

        sql = f"""
        SELECT
            r.schema_name,
            r.table_name,
            i.instance_id,
            i.host,
            i.port,
            i.readonly_username,
            i.readonly_password,
            i.default_schema,
            i.db_type,
            i.version,
            i.enabled
        FROM {self._route_table} AS r
        JOIN {self._instance_table} AS i
          ON i.instance_id = r.instance_id
        WHERE {" OR ".join(predicates)}
        """
        return [
            MetadataRouteRow.model_validate(row)
            for row in self._client.query_all(sql, params)
        ]


class MetadataRouter:
    def __init__(self, repository: MetadataRouteRepository) -> None:
        self._repository = repository

    def resolve(self, tables: list[MysqlTableTarget]) -> EvidenceEnvelope:
        rows = self._repository.find_routes(tables)
        if not rows or len(rows) != len(tables):
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["元数据库未找到一个或多个表的路由信息。"],
            )

        if any(not row.enabled for row in rows):
            disabled = sorted({row.instance_id for row in rows if not row.enabled})
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    f"元数据库中源实例已禁用：{', '.join(disabled)}。"
                ],
            )

        instance_ids = {row.instance_id for row in rows}
        if len(instance_ids) != 1:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"
                ],
            )

        first = rows[0]
        return EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            route=SourceRoute(
                instance_id=first.instance_id,
                db_type=first.db_type,
                version=first.version,
                host=first.host,
                port=first.port,
                default_schema=first.default_schema,
                credentials={
                    "username": first.readonly_username,
                    "password": first.readonly_password,
                },
                schema_names=sorted({row.schema_name for row in rows}),
            ),
        )
