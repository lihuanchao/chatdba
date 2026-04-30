from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode
import urllib.request

from chatdba.db.runtime_mysql import MysqlConnectionConfig, RuntimeMysqlClient
from chatdba.domain.fault_diagnosis import (
    FaultDiagnosisProfile,
    MetricEvidence,
    MetricPoint,
    MetricSeries,
    TopSqlEvidence,
    TopSqlRecord,
)

TOP_SQL_QUERY = """
SELECT
    t.PROCESSLIST_DB AS db,
    t.PROCESSLIST_TIME AS running_seconds,
    es.SQL_TEXT
FROM performance_schema.threads t
JOIN performance_schema.events_statements_current es
    ON t.THREAD_ID = es.THREAD_ID
WHERE t.PROCESSLIST_COMMAND != 'Sleep'
  AND es.SQL_TEXT IS NOT NULL
  AND t.PROCESSLIST_TIME >= %s
ORDER BY t.PROCESSLIST_TIME DESC
LIMIT %s
""".strip()


class MysqlQueryClient(Protocol):
    def query_all(self, sql: str, params: list[object] | None = None):
        raise NotImplementedError


class PrometheusRangeClient(Protocol):
    def range_query(self, *, query: str, start: str, end: str, step: str):
        raise NotImplementedError


class MysqlTopSqlAgent:
    def __init__(
        self,
        *,
        mysql_client: MysqlQueryClient | None = None,
        username: str = "",
        password: str = "",
        port: int = 8801,
        database: str = "performance_schema",
        connect_timeout_seconds: int = 3,
        query_timeout_seconds: int = 8,
        connection_factory=None,
        cursorclass=None,
        min_running_seconds: int = 10,
        limit: int = 10,
    ) -> None:
        self._mysql_client = mysql_client
        self._username = username
        self._password = password
        self._port = port
        self._database = database
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds
        self._connection_factory = connection_factory
        self._cursorclass = cursorclass
        self._min_running_seconds = min_running_seconds
        self._limit = limit

    def analyze(self, profile: FaultDiagnosisProfile) -> TopSqlEvidence:
        if not profile.primary_ip and self._mysql_client is None:
            return TopSqlEvidence(
                status="failure",
                rows=[],
                error_message="缺少数据库管理 IP，无法查询 TopSQL。",
            )
        if self._mysql_client is None and (
            not self._username or not self._connection_factory
        ):
            return TopSqlEvidence(
                status="failure",
                rows=[],
                error_message="TopSQL 数据源未配置连接账号或 PyMySQL 连接工厂。",
            )

        try:
            rows = self._client_for(profile).query_all(
                TOP_SQL_QUERY,
                [self._min_running_seconds, self._limit],
            )
        except Exception as exc:
            return TopSqlEvidence(
                status="failure",
                rows=[],
                error_message=str(exc) or exc.__class__.__name__,
            )

        records = [_top_sql_record_from_row(row) for row in rows[: self._limit]]
        return TopSqlEvidence(
            status="success",
            rows=records,
            summary=f"获取到 {len(records)} 条运行时间超过 {self._min_running_seconds} 秒的 TopSQL。",
        )

    def _client_for(self, profile: FaultDiagnosisProfile) -> MysqlQueryClient:
        if self._mysql_client is not None:
            return self._mysql_client
        return RuntimeMysqlClient(
            connection_factory=self._connection_factory,
            config=MysqlConnectionConfig(
                host=profile.primary_ip or "",
                port=self._port,
                username=self._username,
                password=self._password,
                database=self._database,
                connect_timeout_seconds=self._connect_timeout_seconds,
                query_timeout_seconds=self._query_timeout_seconds,
            ),
            cursorclass=self._cursorclass,
        )


class PrometheusHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        opener=None,
        timeout_seconds: int = 8,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._opener = opener or urllib.request.urlopen
        self._timeout_seconds = timeout_seconds

    def range_query(self, *, query: str, start: str, end: str, step: str):
        url = (
            f"{self._base_url}/api/v1/query_range?"
            + urlencode(
                {
                    "query": query,
                    "start": start,
                    "end": end,
                    "step": step,
                }
            )
        )
        request = urllib.request.Request(url, method="GET")
        with self._opener(request, timeout=self._timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("Prometheus response is not a JSON object.")
        return payload


class PrometheusMetricAgent:
    def __init__(
        self,
        *,
        client: PrometheusRangeClient | None = None,
        base_url: str = "",
        opener=None,
        timeout_seconds: int = 8,
        step_seconds: int = 60,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._step_seconds = step_seconds

    def analyze(self, profile: FaultDiagnosisProfile) -> MetricEvidence:
        if not profile.primary_ip:
            return MetricEvidence(
                status="failure",
                metrics=[],
                error_message="缺少数据库服务器 IP，无法查询监控指标。",
            )
        if self._client is None and not self._base_url:
            return MetricEvidence(
                status="failure",
                metrics=[],
                error_message="Prometheus 数据源未配置。",
            )

        query = _cpu_usage_query(profile.primary_ip)
        start = _to_prometheus_utc(profile.start_time)
        end = _to_prometheus_utc(profile.end_time)
        step = f"{self._step_seconds}s"
        try:
            payload = self._client_for().range_query(
                query=query,
                start=start,
                end=end,
                step=step,
            )
            series = _metric_series_from_payload(
                payload,
                metric_name="cpu_usage",
                default_ip=profile.primary_ip,
                unit="%",
            )
        except Exception as exc:
            return MetricEvidence(
                status="failure",
                metrics=[],
                error_message=str(exc) or exc.__class__.__name__,
            )

        return MetricEvidence(
            status="success",
            metrics=series,
            summary=_metric_summary(series),
        )

    def _client_for(self) -> PrometheusRangeClient:
        if self._client is not None:
            return self._client
        return PrometheusHttpClient(
            base_url=self._base_url,
            opener=self._opener,
            timeout_seconds=self._timeout_seconds,
        )


def _top_sql_record_from_row(row: object) -> TopSqlRecord:
    data = row if isinstance(row, dict) else {}
    return TopSqlRecord(
        database=_optional_str(data.get("db") or data.get("PROCESSLIST_DB")),
        running_seconds=_optional_float(
            data.get("running_seconds") or data.get("PROCESSLIST_TIME")
        ),
        sql_text=str(data.get("SQL_TEXT") or data.get("sql_text") or ""),
    )


def _metric_series_from_payload(
    payload: object,
    *,
    metric_name: str,
    default_ip: str,
    unit: str,
) -> list[MetricSeries]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []

    series: list[MetricSeries] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        metric_labels = metric if isinstance(metric, dict) else {}
        ip = str(metric_labels.get("ip") or default_ip)
        values = []
        raw_values = item.get("values")
        if isinstance(raw_values, list):
            for raw_point in raw_values:
                point = _metric_point_from_raw(raw_point)
                if point is not None:
                    values.append(point)
        series.append(
            MetricSeries(
                metric_name=metric_name,
                ip=ip,
                unit=unit,
                values=values,
            )
        )
    return series


def _metric_point_from_raw(raw_point: object) -> MetricPoint | None:
    if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
        return None
    try:
        timestamp = int(float(raw_point[0]))
        value = float(raw_point[1])
    except (TypeError, ValueError):
        return None
    return MetricPoint(timestamp=timestamp, value=value)


def _metric_summary(series: list[MetricSeries]) -> str:
    if not series:
        return "Prometheus 查询成功，但未返回 CPU 使用率数据。"
    peaks = [
        max((point.value for point in item.values), default=0.0)
        for item in series
    ]
    peak = max(peaks, default=0.0)
    return f"CPU 使用率峰值为 {peak:g}%。"


def _cpu_usage_query(ip: str) -> str:
    return (
        '100 - (avg by(ip) (rate(node_cpu_seconds_total{mode="idle", '
        f'ip="{ip}"}}[10m])) * 100)'
    )


def _to_prometheus_utc(value: str) -> str:
    local_time = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    utc_time = local_time - timedelta(hours=8)
    return utc_time.strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
