from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import TableReference


class StaticMetadataRepository:
    def __init__(self, default_schema: str = "default") -> None:
        self._default_schema = default_schema

    def resolve_tables(self, tables: list[TableReference]) -> list[MysqlTableTarget]:
        return [
            MysqlTableTarget(
                schema_name=table.schema_name,
                table_name=table.table_name,
            )
            for table in tables
        ]
