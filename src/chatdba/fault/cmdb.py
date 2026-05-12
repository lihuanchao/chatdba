from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CmdbHostRecord:
    management_ip: str
    business_ip: str
    system_name: str


class CmdbQueryClient(Protocol):
    def query_all(self, sql: str, params: list[object] | None = None):
        raise NotImplementedError


class CmdbHostRepository:
    def __init__(
        self,
        *,
        client: CmdbQueryClient,
        table_name: str = "cmd_hosts",
    ) -> None:
        self._client = client
        self._table_name = _safe_identifier(table_name)

    def resolve_by_management_ip(self, management_ip: str) -> CmdbHostRecord | None:
        rows = self._client.query_all(
            f"""
            SELECT management_ip, business_ip, system_name
            FROM {self._table_name}
            WHERE management_ip = %s
            LIMIT 1
            """.strip(),
            [management_ip],
        )
        if not rows:
            return None
        row = rows[0] if isinstance(rows[0], dict) else {}
        business_ip = _required_text(row.get("business_ip"))
        system_name = _required_text(row.get("system_name"))
        return CmdbHostRecord(
            management_ip=management_ip,
            business_ip=business_ip,
            system_name=system_name,
        )


def _required_text(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise RuntimeError("CMDB host record is missing required fields.")
    return text


def _safe_identifier(value: str) -> str:
    if not value or not value.replace("_", "").isalnum():
        raise ValueError(f"Invalid CMDB table name: {value}")
    return value
