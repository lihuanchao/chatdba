from typing import Protocol

from pydantic import BaseModel

from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.db.route_errors import MULTI_TABLE_SCHEMA_MARKER
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute


def _same_identifier(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


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
            if table.schema_name:
                predicates.append(
                    "(LOWER(r.schema_name) = LOWER(%s) "
                    "AND LOWER(r.table_name) = LOWER(%s))"
                )
                params.extend([table.schema_name, table.table_name])
            else:
                predicates.append("(LOWER(r.table_name) = LOWER(%s))")
                params.append(table.table_name)

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
        ORDER BY i.instance_id, r.schema_name, r.table_name
        """
        return [
            MetadataRouteRow.model_validate(row)
            for row in self._client.query_all(sql, params)
        ]


class MetadataRouter:
    def __init__(self, repository: MetadataRouteRepository) -> None:
        self._repository = repository

    def resolve(self, tables: list[MysqlTableTarget]) -> EvidenceEnvelope:
        envelope, _resolved_tables = self.resolve_with_tables(tables)
        return envelope

    def resolve_with_tables(
        self,
        tables: list[MysqlTableTarget],
    ) -> tuple[EvidenceEnvelope, list[MysqlTableTarget]]:
        rows = self._repository.find_routes(tables)
        candidate_rows = {
            index: self._rows_for_target(rows, target)
            for index, target in enumerate(tables)
        }
        if not rows or any(not matches for matches in candidate_rows.values()):
            if self._should_request_schema_for_missing_routes(tables):
                return EvidenceEnvelope(
                    status=EvidenceStatus.SQL_ONLY,
                    missing_evidence=["route_info", "explain_json", "create_table"],
                    collection_errors=[
                        MULTI_TABLE_SCHEMA_MARKER
                        + ", ".join(self._unqualified_table_names(tables))
                    ],
                ), []
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["元数据库未找到一个或多个表的路由信息。"],
            ), []

        enabled_candidates = {
            index: [row for row in matches if row.enabled]
            for index, matches in candidate_rows.items()
        }
        if any(not matches for matches in enabled_candidates.values()):
            disabled = sorted(
                {
                    row.instance_id
                    for matches in candidate_rows.values()
                    for row in matches
                    if not row.enabled
                }
            )
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    f"元数据库中源实例已禁用：{', '.join(disabled)}。"
                ],
            ), []

        ambiguous_unqualified = self._ambiguous_unqualified_tables(
            tables,
            enabled_candidates,
        )
        if ambiguous_unqualified:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    "以下表名在元数据库中存在重复，请补充库名后重试："
                    + ", ".join(sorted(ambiguous_unqualified))
                ],
            ), []

        selection = self._select_route_plan(tables, enabled_candidates)
        if selection is None:
            message = (
                MULTI_TABLE_SCHEMA_MARKER
                + ", ".join(self._unqualified_table_names(tables))
                if self._has_common_instance(enabled_candidates)
                else "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"
            )
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[message],
            ), []

        selected_rows, connection_schema = selection
        instance_ids = {row.instance_id for row in selected_rows}
        if len(instance_ids) != 1:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"
                ],
            ), []

        first = selected_rows[0]
        resolved_tables = [
            MysqlTableTarget(schema_name=row.schema_name, table_name=row.table_name)
            for row in selected_rows
        ]
        return EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            route=SourceRoute(
                instance_id=first.instance_id,
                db_type=first.db_type,
                version=first.version,
                host=first.host,
                port=first.port,
                default_schema=connection_schema or first.default_schema,
                credentials={
                    "username": first.readonly_username,
                    "password": first.readonly_password,
                },
                schema_names=sorted({row.schema_name for row in selected_rows}),
            ),
        ), resolved_tables

    def _rows_for_target(
        self,
        rows: list[MetadataRouteRow],
        target: MysqlTableTarget,
    ) -> list[MetadataRouteRow]:
        if target.schema_name:
            return [
                row
                for row in rows
                if _same_identifier(row.schema_name, target.schema_name)
                and _same_identifier(row.table_name, target.table_name)
            ]
        return [row for row in rows if _same_identifier(row.table_name, target.table_name)]

    def _select_route_plan(
        self,
        tables: list[MysqlTableTarget],
        candidates: dict[int, list[MetadataRouteRow]],
    ) -> tuple[list[MetadataRouteRow], str | None] | None:
        instance_ids = sorted(
            {
                row.instance_id
                for matches in candidates.values()
                for row in matches
            }
        )
        plans: list[tuple[int, str, str | None, list[MetadataRouteRow]]] = []
        for instance_id in instance_ids:
            plan = self._plan_for_instance(instance_id, tables, candidates)
            if plan is None:
                continue
            selected_rows, connection_schema, uses_default_schema = plan
            plans.append(
                (
                    1 if uses_default_schema else 0,
                    instance_id,
                    connection_schema,
                    selected_rows,
                )
            )

        if not plans:
            return None

        best = sorted(
            plans,
            key=lambda item: (
                -item[0],
                item[1],
                item[2] or "",
                [row.schema_name for row in item[3]],
            ),
        )[0]
        return best[3], best[2]

    def _ambiguous_unqualified_tables(
        self,
        tables: list[MysqlTableTarget],
        candidates: dict[int, list[MetadataRouteRow]],
    ) -> list[str]:
        ambiguous: list[str] = []
        for index, table in enumerate(tables):
            if table.schema_name:
                continue
            route_keys = {
                (row.instance_id, row.schema_name)
                for row in candidates[index]
            }
            if len(route_keys) > 1:
                ambiguous.append(tables[index].table_name)
        return ambiguous

    def _unqualified_table_names(self, tables: list[MysqlTableTarget]) -> list[str]:
        names: list[str] = []
        for table in tables:
            if table.schema_name or table.table_name in names:
                continue
            names.append(table.table_name)
        return names or ["相关表"]

    def _should_request_schema_for_missing_routes(
        self,
        tables: list[MysqlTableTarget],
    ) -> bool:
        return len(self._unqualified_table_names(tables)) > 1

    def _has_common_instance(
        self,
        candidates: dict[int, list[MetadataRouteRow]],
    ) -> bool:
        instance_sets = [
            {row.instance_id for row in matches}
            for matches in candidates.values()
        ]
        if not instance_sets:
            return False
        common_instances = set(instance_sets[0])
        for instance_set in instance_sets[1:]:
            common_instances &= instance_set
        return bool(common_instances)

    def _plan_for_instance(
        self,
        instance_id: str,
        tables: list[MysqlTableTarget],
        candidates: dict[int, list[MetadataRouteRow]],
    ) -> tuple[list[MetadataRouteRow], str | None, bool] | None:
        instance_candidates = {
            index: [row for row in matches if row.instance_id == instance_id]
            for index, matches in candidates.items()
        }
        if any(not matches for matches in instance_candidates.values()):
            return None

        unqualified_indexes = [
            index for index, table in enumerate(tables) if not table.schema_name
        ]
        connection_schema: str | None = None
        uses_default_schema = False
        if unqualified_indexes:
            common_schemas = set(
                row.schema_name for row in instance_candidates[unqualified_indexes[0]]
            )
            for index in unqualified_indexes[1:]:
                common_schemas &= {
                    row.schema_name for row in instance_candidates[index]
                }
            if not common_schemas:
                return None
            default_schema = instance_candidates[unqualified_indexes[0]][0].default_schema
            if default_schema and default_schema in common_schemas:
                connection_schema = default_schema
                uses_default_schema = True
            else:
                connection_schema = sorted(common_schemas)[0]

        selected_rows: list[MetadataRouteRow] = []
        for index, table in enumerate(tables):
            matches = instance_candidates[index]
            if table.schema_name:
                selected_rows.append(matches[0])
                continue
            chosen = next(
                (row for row in matches if row.schema_name == connection_schema),
                None,
            )
            if chosen is None:
                return None
            selected_rows.append(chosen)

        return selected_rows, connection_schema, uses_default_schema
