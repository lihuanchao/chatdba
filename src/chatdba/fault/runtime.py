from dataclasses import dataclass
import json
from typing import Any

from chatdba.fault.agents import (
    MysqlTopSqlAgent,
    PrometheusMcpClient,
    PrometheusMetricAgent,
)
from chatdba.fault.cmdb import CmdbHostRepository


@dataclass(frozen=True)
class FaultDiagnosisRuntime:
    top_sql_agent: MysqlTopSqlAgent
    metric_agent: PrometheusMetricAgent
    cmdb_resolver: CmdbHostRepository | None = None


def build_fault_diagnosis_runtime(
    settings,
    *,
    pymysql_module: Any | None = None,
) -> FaultDiagnosisRuntime:
    pymysql_runtime = _load_pymysql_runtime(settings, pymysql_module)
    top_sql_agent = MysqlTopSqlAgent(
        username=getattr(settings, "fault_top_sql_user", ""),
        password=getattr(settings, "fault_top_sql_password", ""),
        port=int(getattr(settings, "fault_top_sql_port", 8801)),
        database=getattr(settings, "fault_top_sql_database", "performance_schema"),
        connect_timeout_seconds=int(getattr(settings, "mysql_connect_timeout_seconds", 3)),
        query_timeout_seconds=int(getattr(settings, "mysql_query_timeout_seconds", 8)),
        connection_factory=pymysql_runtime["connect"],
        cursorclass=pymysql_runtime["cursorclass"],
        min_running_seconds=int(
            getattr(settings, "fault_top_sql_min_running_seconds", 10)
        ),
        limit=int(getattr(settings, "fault_top_sql_limit", 10)),
    )
    metric_agent = PrometheusMetricAgent(
        mcp_client=_build_prometheus_mcp_client(settings),
        base_url=getattr(settings, "fault_prometheus_base_url", ""),
        timeout_seconds=int(getattr(settings, "fault_prometheus_timeout_seconds", 8)),
        step_seconds=int(getattr(settings, "fault_metric_step_seconds", 60)),
        active_threads_query_template=getattr(
            settings,
            "fault_active_threads_query_template",
            None,
        ),
        slow_sql_count_query_template=getattr(
            settings,
            "fault_slow_sql_count_query_template",
            None,
        ),
    )
    return FaultDiagnosisRuntime(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
        cmdb_resolver=_build_cmdb_resolver(settings, pymysql_module),
    )


def _load_pymysql_runtime(settings, pymysql_module: Any | None) -> dict[str, Any | None]:
    if not (
        getattr(settings, "fault_top_sql_user", "")
        and getattr(settings, "fault_top_sql_password", "")
    ):
        return {"connect": None, "cursorclass": None}

    module = pymysql_module
    if module is None:
        try:
            import pymysql as module
        except Exception:
            return {"connect": None, "cursorclass": None}

    connect = getattr(module, "connect", None)
    cursorclass = getattr(getattr(module, "cursors", None), "DictCursor", None)
    return {
        "connect": connect if callable(connect) else None,
        "cursorclass": cursorclass,
    }


def _build_prometheus_mcp_client(settings) -> PrometheusMcpClient | None:
    sse_url = str(getattr(settings, "fault_prometheus_mcp_sse_url", "") or "").strip()
    if not sse_url:
        return None
    headers = _parse_mcp_headers_json(
        str(getattr(settings, "fault_prometheus_mcp_headers_json", "{}") or "{}")
    )
    return PrometheusMcpClient(
        sse_url=sse_url,
        headers=headers,
        timeout_seconds=int(
            getattr(settings, "fault_prometheus_mcp_timeout_seconds", 50)
        ),
        sse_read_timeout_seconds=int(
            getattr(settings, "fault_prometheus_mcp_sse_read_timeout_seconds", 50)
        ),
    )


def _build_cmdb_resolver(
    settings,
    pymysql_module: Any | None,
) -> CmdbHostRepository | None:
    table_name = str(getattr(settings, "fault_cmdb_table", "cmd_hosts") or "").strip()
    if not table_name:
        return None
    client = _build_metadata_mysql_client(settings, pymysql_module)
    if client is None:
        return None
    return CmdbHostRepository(client=client, table_name=table_name)


def _build_metadata_mysql_client(settings, pymysql_module: Any | None):
    if not (
        getattr(settings, "metadata_mysql_host", "")
        and getattr(settings, "metadata_mysql_user", "")
        and getattr(settings, "metadata_mysql_database", "")
    ):
        return None

    module = pymysql_module
    if module is None:
        try:
            import pymysql as module
        except Exception:
            return None

    connect = getattr(module, "connect", None)
    cursorclass = getattr(getattr(module, "cursors", None), "DictCursor", None)
    if not callable(connect):
        return None

    from chatdba.db.runtime_mysql import MysqlConnectionConfig, RuntimeMysqlClient

    return RuntimeMysqlClient(
        connection_factory=connect,
        config=MysqlConnectionConfig(
            host=getattr(settings, "metadata_mysql_host", ""),
            port=int(getattr(settings, "metadata_mysql_port", 3306)),
            username=getattr(settings, "metadata_mysql_user", ""),
            password=getattr(settings, "metadata_mysql_password", ""),
            database=getattr(settings, "metadata_mysql_database", ""),
            connect_timeout_seconds=int(
                getattr(settings, "mysql_connect_timeout_seconds", 3)
            ),
            query_timeout_seconds=int(
                getattr(settings, "mysql_query_timeout_seconds", 8)
            ),
        ),
        cursorclass=cursorclass,
    )


def _parse_mcp_headers_json(value: str) -> dict[str, str]:
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    headers: dict[str, str] = {}
    for key, item in payload.items():
        if item is None:
            continue
        headers[str(key)] = str(item)
    return headers
