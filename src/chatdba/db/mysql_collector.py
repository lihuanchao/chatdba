import json
from typing import Protocol

from pydantic import BaseModel, Field


class MysqlClient(Protocol):
    def query_one(self, sql: str) -> dict[str, object]:
        ...


class MysqlTableTarget(BaseModel):
    schema_name: str
    table_name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class MysqlEvidence(BaseModel):
    explain_json: dict[str, object]
    create_tables: dict[str, str] = Field(default_factory=dict)


class MysqlEvidenceCollector:
    def __init__(self, client: MysqlClient) -> None:
        self._client = client

    def collect(self, sql: str, tables: list[MysqlTableTarget]) -> MysqlEvidence:
        explain_row = self._client.query_one(f"EXPLAIN FORMAT=JSON {sql}")
        explain_raw = str(explain_row["EXPLAIN"])
        create_tables: dict[str, str] = {}

        for table in tables:
            row = self._client.query_one(
                f"SHOW CREATE TABLE `{table.schema_name}`.`{table.table_name}`"
            )
            create_tables[table.qualified_name] = str(row["Create Table"])

        return MysqlEvidence(
            explain_json=json.loads(explain_raw),
            create_tables=create_tables,
        )

