from dataclasses import dataclass
from typing import Any

from chatdba.fault.agents import MysqlTopSqlAgent, PrometheusMetricAgent


@dataclass(frozen=True)
class FaultDiagnosisRuntime:
    top_sql_agent: MysqlTopSqlAgent
    metric_agent: PrometheusMetricAgent


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
        base_url=getattr(settings, "fault_prometheus_base_url", ""),
        timeout_seconds=int(getattr(settings, "fault_prometheus_timeout_seconds", 8)),
        step_seconds=int(getattr(settings, "fault_metric_step_seconds", 60)),
    )
    return FaultDiagnosisRuntime(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
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
