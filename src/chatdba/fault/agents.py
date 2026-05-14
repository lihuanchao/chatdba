from __future__ import annotations

import json
import itertools
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    SCHEMA_NAME as `数据库名`,
    DIGEST_TEXT as `SQL语句摘要`,
    COUNT_STAR as `执行次数`,
    ROUND(AVG_TIMER_WAIT/1000000000000, 4) as `平均执行时间(秒)`,
    ROUND(SUM_TIMER_WAIT/1000000000000, 4) as `总执行时间(秒)`
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME IS NOT NULL
  AND SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys')
  AND DIGEST_TEXT IS NOT NULL
  AND LAST_SEEN > %s
  AND LAST_SEEN < %s
ORDER BY AVG_TIMER_WAIT DESC
LIMIT %s
""".strip()

_DEFAULT_ACTIVE_THREADS_QUERY_TEMPLATE = (
    'ctg_paas_30202624250003{sysCode="database_prod",'
    'tenant_id="100011",ip="{management_ip}"}'
)
_DEFAULT_SLOW_SQL_COUNT_QUERY_TEMPLATE = (
    'increase(mysql_global_status_slow_queries{ip="{management_ip}"}[1m])'
)


class MysqlQueryClient(Protocol):
    def query_all(self, sql: str, params: list[object] | None = None):
        raise NotImplementedError


class PrometheusRangeClient(Protocol):
    def range_query(self, *, query: str, start: str, end: str, step: str):
        raise NotImplementedError


class PrometheusMcpClient:
    def __init__(
        self,
        *,
        sse_url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 50,
        sse_read_timeout_seconds: int = 50,
        opener=None,
    ) -> None:
        self._sse_url = sse_url
        self._headers = headers or {}
        self._timeout_seconds = timeout_seconds
        self._sse_read_timeout_seconds = sse_read_timeout_seconds
        self._opener = opener or urllib.request.urlopen
        self._session_id: str | None = None
        self._messages_url: str | None = None
        self._request_ids = itertools.count(1)
        self._tool_name: str | None = None

    def range_query(self, *, query: str, start: str, end: str, step: str):
        self._ensure_session()
        payload = self._jsonrpc_call(
            "tools/call",
            {
                "name": self._resolve_range_tool_name(),
                "arguments": {
                    "query": query,
                    "start": start,
                    "end": end,
                    "step": step,
                },
            },
        )
        return _normalize_mcp_result_to_prometheus_payload(payload)

    def _ensure_session(self) -> None:
        if self._messages_url and self._session_id:
            return
        self._messages_url = self._discover_messages_url()
        init_response = self._jsonrpc_call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "chatdba", "version": "0.1.0"},
            },
            require_session=False,
        )
        if not isinstance(init_response, dict):
            raise TypeError("MCP initialize result is invalid.")
        self._jsonrpc_notify("notifications/initialized")

    def _resolve_range_tool_name(self) -> str:
        if self._tool_name:
            return self._tool_name
        result = self._jsonrpc_call("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list did not return tools.")
        names = {
            str(item.get("name"))
            for item in tools
            if isinstance(item, dict) and item.get("name")
        }
        for candidate in ("execute_range_query", "query_range", "range_query"):
            if candidate in names:
                self._tool_name = candidate
                return candidate
        raise RuntimeError("Prometheus MCP range query tool not found.")

    def _discover_messages_url(self) -> str:
        request = urllib.request.Request(self._sse_url, method="GET")
        for key, value in self._headers.items():
            request.add_header(key, value)
        with self._opener(request, timeout=self._sse_read_timeout_seconds) as response:
            headers = _response_headers_dict(response)
            session_id = headers.get("mcp-session-id")
            endpoint_data = _read_sse_event_data(response, event_name="endpoint")
        if not endpoint_data:
            raise RuntimeError("MCP SSE endpoint did not provide messages URL.")
        if endpoint_data.startswith("http://") or endpoint_data.startswith("https://"):
            messages_url = endpoint_data
        else:
            base = self._sse_url.rstrip("/")
            messages_url = f"{base}{endpoint_data}"
        if not session_id:
            session_id = _extract_session_id_from_messages_url(messages_url)
        self._session_id = session_id
        return messages_url

    def _jsonrpc_call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        require_session: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._request_ids),
            "method": method,
            "params": params,
        }
        response = self._post_json(payload, require_session=require_session)
        if not isinstance(response, dict):
            raise TypeError("MCP response is not a JSON object.")
        if "error" in response:
            message = _stringify_jsonrpc_error(response["error"])
            raise RuntimeError(f"MCP {method} failed: {message}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise TypeError(f"MCP {method} returned invalid result.")
        return result

    def _jsonrpc_notify(self, method: str) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {},
        }
        self._post_json(payload, require_session=True)

    def _post_json(
        self,
        payload: dict[str, Any],
        *,
        require_session: bool,
    ) -> dict[str, Any] | None:
        if not self._messages_url:
            raise RuntimeError("MCP messages URL not initialized.")
        request = urllib.request.Request(
            self._messages_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        for key, value in self._headers.items():
            request.add_header(key, value)
        if require_session and self._session_id:
            request.add_header("Mcp-Session-Id", self._session_id)
        with self._opener(request, timeout=self._timeout_seconds) as response:
            headers = _response_headers_dict(response)
            if not self._session_id:
                self._session_id = headers.get("mcp-session-id")
            body = response.read().decode("utf-8").strip()
        if not body:
            return None
        return json.loads(body)


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
            ts_min, ts_max = _top_sql_time_bounds(profile)
            rows = self._client_for(profile).query_all(
                TOP_SQL_QUERY,
                [ts_min, ts_max, self._limit],
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
            summary=f"获取到 {len(records)} 条告警前 30 分钟内平均执行时间最高的 TopSQL 摘要。",
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
        mcp_client: PrometheusRangeClient | None = None,
        client: PrometheusRangeClient | None = None,
        base_url: str = "",
        opener=None,
        timeout_seconds: int = 8,
        step_seconds: int = 60,
        active_threads_query_template: str | None = None,
        slow_sql_count_query_template: str | None = None,
    ) -> None:
        self._mcp_client = mcp_client
        self._client = client
        self._base_url = base_url
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._step_seconds = step_seconds
        self._active_threads_query_template = (
            active_threads_query_template or _DEFAULT_ACTIVE_THREADS_QUERY_TEMPLATE
        )
        self._slow_sql_count_query_template = (
            slow_sql_count_query_template or _DEFAULT_SLOW_SQL_COUNT_QUERY_TEMPLATE
        )

    def analyze(self, profile: FaultDiagnosisProfile) -> MetricEvidence:
        metric_ip = profile.business_ip
        if not metric_ip:
            return MetricEvidence(
                status="failure",
                metrics=[],
                error_message=(
                    "缺少业务 IP，无法查询监控指标；请在 CMDB 表中维护管理 IP 到业务 IP 的映射。"
                ),
            )
        if self._mcp_client is None and self._client is None and not self._base_url:
            return MetricEvidence(
                status="failure",
                metrics=[],
                error_message="Prometheus 数据源未配置。",
            )

        start = _to_prometheus_utc(profile.start_time, profile.timezone)
        end = _to_prometheus_utc(profile.end_time, profile.timezone)
        step = f"{self._step_seconds}s"
        errors: list[str] = []
        missing_metrics: list[str] = []
        series: list[MetricSeries] = []
        for spec in self._metric_specs(profile):
            collected = self._query_metric_spec(
                spec=spec,
                start=start,
                end=end,
                step=step,
                errors=errors,
                missing_metrics=missing_metrics,
            )
            series.extend(collected)

        if not series:
            return MetricEvidence(
                status="failure",
                metrics=[],
                missing_metrics=missing_metrics,
                error_message=" | ".join(errors or missing_metrics)
                or "prometheus_query_failed",
            )

        return MetricEvidence(
            status="success",
            metrics=series,
            missing_metrics=missing_metrics,
            summary=_metric_summary(series),
            error_message=" | ".join(missing_metrics) if missing_metrics else None,
        )

    def _metric_specs(self, profile: FaultDiagnosisProfile) -> list[dict[str, str]]:
        business_ip = profile.business_ip or ""
        management_ip = profile.management_ip or profile.primary_ip or ""
        return [
            {
                "metric_name": "cpu_usage",
                "query": _cpu_usage_query(business_ip),
                "default_ip": business_ip,
                "unit": "%",
            },
            {
                "metric_name": "active_threads",
                "query": _format_metric_query(
                    self._active_threads_query_template,
                    ip=management_ip,
                    business_ip=business_ip,
                    management_ip=management_ip,
                ),
                "default_ip": management_ip,
                "unit": "count",
            },
            {
                "metric_name": "slow_sql_count",
                "query": _format_metric_query(
                    self._slow_sql_count_query_template,
                    ip=management_ip,
                    business_ip=business_ip,
                    management_ip=management_ip,
                ),
                "default_ip": management_ip,
                "unit": "count",
            },
        ]

    def _query_metric_spec(
        self,
        *,
        spec: dict[str, str],
        start: str,
        end: str,
        step: str,
        errors: list[str],
        missing_metrics: list[str],
    ) -> list[MetricSeries]:
        last_errors: list[str] = []
        empty_result_seen = False
        for candidate in self._clients_in_order():
            try:
                payload = candidate.range_query(
                    query=spec["query"],
                    start=start,
                    end=end,
                    step=step,
                )
                result = _metric_series_from_payload(
                    payload,
                    metric_name=spec["metric_name"],
                    default_ip=spec["default_ip"],
                    unit=spec["unit"],
                )
                if result:
                    return result
                empty_result_seen = True
                continue
            except Exception as exc:
                last_errors.append(str(exc) or exc.__class__.__name__)
                continue
        reason = "; ".join(last_errors)
        if empty_result_seen:
            reason = f"未返回数据{'; ' + reason if reason else ''}"
        message = f"{spec['metric_name']}: {reason or '未返回数据'}"
        errors.append(message)
        missing_metrics.append(message)
        return []

    def _clients_in_order(self) -> list[PrometheusRangeClient]:
        clients: list[PrometheusRangeClient] = []
        if self._mcp_client is not None:
            clients.append(self._mcp_client)
        if self._client is not None or bool(self._base_url):
            clients.append(self._client_for_http())
        return clients

    def _client_for(self) -> PrometheusRangeClient:
        # backward compatible alias for existing tests/callers
        return self._client_for_http()

    def _client_for_http(self) -> PrometheusRangeClient:
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
        database=_optional_str(
            data.get("数据库名") or data.get("db") or data.get("SCHEMA_NAME")
        ),
        execution_count=_optional_int(data.get("执行次数") or data.get("COUNT_STAR")),
        avg_execution_seconds=_optional_float(
            data.get("平均执行时间(秒)") or data.get("avg_execution_seconds")
        ),
        total_execution_seconds=_optional_float(
            data.get("总执行时间(秒)") or data.get("total_execution_seconds")
        ),
        sql_text=str(
            data.get("SQL语句摘要")
            or data.get("DIGEST_TEXT")
            or data.get("SQL_TEXT")
            or data.get("sql_text")
            or ""
        ),
    )


def _top_sql_time_bounds(profile: FaultDiagnosisProfile) -> tuple[str, str]:
    end_time = profile.alert_time or profile.end_time
    ts_max = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    ts_min = ts_max - timedelta(minutes=30)
    return _format_mysql_time(ts_min), _format_mysql_time(ts_max)


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
        return "Prometheus 查询成功，但未返回监控指标数据。"
    metric_labels = {
        "cpu_usage": "CPU 使用率",
        "active_threads": "活跃线程数",
        "slow_sql_count": "慢 SQL 数",
    }
    parts = []
    for item in series:
        peak = max((point.value for point in item.values), default=0.0)
        label = metric_labels.get(item.metric_name, item.metric_name)
        unit = item.unit or ""
        parts.append(f"{label}峰值为 {peak:g}{unit}")
    return "，".join(parts) + "。"


def _cpu_usage_query(ip: str) -> str:
    return (
        '100 - (avg by(ip) (rate(node_cpu_seconds_total{mode="idle", '
        f'ip="{ip}"}}[10m])) * 100)'
    )


def _format_metric_query(
    template: str,
    *,
    ip: str,
    business_ip: str,
    management_ip: str,
) -> str:
    return (
        template.replace("{management_ip}", management_ip)
        .replace("{business_ip}", business_ip)
        .replace("{ip}", ip)
    )


def _to_prometheus_utc(value: str, source_timezone: str = "Asia/Shanghai") -> str:
    local_time = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    try:
        source_tz = ZoneInfo(source_timezone or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        source_tz = ZoneInfo("Asia/Shanghai")
    utc_time = local_time.replace(tzinfo=source_tz).astimezone(ZoneInfo("UTC"))
    return utc_time.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_mysql_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _response_headers_dict(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    return {}


def _read_sse_event_data(response: Any, *, event_name: str | None = None) -> str:
    current_event: str | None = None
    data_lines: list[str] = []
    while True:
        raw = response.readline()
        if raw in (b"", ""):
            break
        line = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if not line:
            if data_lines and (event_name is None or current_event == event_name):
                return "\n".join(data_lines)
            current_event = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
            continue
    return ""


def _extract_session_id_from_messages_url(messages_url: str) -> str | None:
    marker = "session_id="
    index = messages_url.find(marker)
    if index == -1:
        return None
    raw = messages_url[index + len(marker):]
    if "&" in raw:
        raw = raw.split("&", 1)[0]
    return raw or None


def _stringify_jsonrpc_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code is not None:
            return f"code={code}, message={message}"
        if message:
            return str(message)
    return str(error)


def _normalize_mcp_result_to_prometheus_payload(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") is True:
        message = _extract_tool_message_text(result) or "MCP tool returned error."
        raise RuntimeError(message)

    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            candidate = _json_from_text_payload(text)
            if isinstance(candidate, dict):
                normalized = _normalize_known_metric_payload(candidate)
                if normalized is not None:
                    return normalized
        raise RuntimeError("MCP tools/call returned no parseable metric payload.")

    normalized = _normalize_known_metric_payload(result)
    if normalized is not None:
        return normalized

    raise RuntimeError("Unsupported MCP metric payload format.")


def _extract_tool_message_text(result: dict[str, Any]) -> str | None:
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _json_from_text_payload(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_known_metric_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(payload.get("result"), list):
        return {"status": "success", "data": {"result": payload["result"]}}

    if isinstance(payload.get("data"), dict):
        data = payload["data"]
        if isinstance(data.get("result"), list):
            return {"status": "success", "data": {"result": data["result"]}}

    data = payload.get("data")
    if isinstance(data, dict):
        metrics = data.get("metrics")
        if isinstance(metrics, list):
            return {
                "status": "success",
                "data": {"result": _metric_result_from_agent_metrics(metrics)},
            }
    if isinstance(payload.get("metrics"), list):
        return {
            "status": "success",
            "data": {"result": _metric_result_from_agent_metrics(payload["metrics"])},
        }
    return None


def _metric_result_from_agent_metrics(metrics: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        ip = item.get("ip")
        metric = {"ip": str(ip)} if ip is not None else {}
        values: list[list[Any]] = []
        raw_values = item.get("values")
        if isinstance(raw_values, list):
            for point in raw_values:
                if not isinstance(point, dict):
                    continue
                ts = point.get("timestamp")
                val = point.get("value")
                if ts is None or val is None:
                    continue
                values.append([ts, str(val)])
        result.append({"metric": metric, "values": values})
    return result


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
