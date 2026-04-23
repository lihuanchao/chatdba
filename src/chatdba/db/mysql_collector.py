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


def _parse_explain_payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise TypeError(
                f"Expected EXPLAIN payload to decode to a dict, got {type(parsed).__name__}"
            )
        return parsed
    raise TypeError(
        "Expected EXPLAIN payload as dict, str, bytes, or bytearray; "
        f"got {type(value).__name__}"
    )


def _quote_mysql_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


class MysqlEvidenceCollector:
    def __init__(self, client: MysqlClient) -> None:
        self._client = client

    def collect(self, sql: str, tables: list[MysqlTableTarget]) -> MysqlEvidence:
        explain_row = self._client.query_one(f"EXPLAIN FORMAT=JSON {sql}")
        create_tables: dict[str, str] = {}

        for table in tables:
            row = self._client.query_one(
                "SHOW CREATE TABLE "
                f"{_quote_mysql_identifier(table.schema_name)}."
                f"{_quote_mysql_identifier(table.table_name)}"
            )
            create_tables[table.qualified_name] = str(row["Create Table"])

        return MysqlEvidence(
            explain_json=_parse_explain_payload(explain_row["EXPLAIN"]),
            create_tables=create_tables,
        )
