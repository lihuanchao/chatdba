import json
from urllib.parse import parse_qs, urlparse

from chatdba.domain.fault_diagnosis import FaultDiagnosisProfile
from chatdba.fault.agents import (
    MysqlTopSqlAgent,
    PrometheusHttpClient,
    PrometheusMcpClient,
    PrometheusMetricAgent,
)


def make_profile() -> FaultDiagnosisProfile:
    return FaultDiagnosisProfile(
        input_text="订单系统数据库 CPU 告警",
        system_name="订单系统",
        management_ip="10.186.17.54",
        business_ip="10.186.17.55",
        primary_ip="10.186.17.54",
        alert_time="2026-04-30 15:00:00",
        start_time="2026-04-30 14:00:00",
        end_time="2026-04-30 15:00:00",
        query_background="订单系统数据库故障诊断",
    )


class RecordingMysqlClient:
    def __init__(self):
        self.calls = []

    def query_all(self, sql: str, params=None):
        self.calls.append((sql, params))
        return [
            {
                "数据库名": "orders",
                "SQL语句摘要": "select * from orders where status = ?",
                "执行次数": 12,
                "平均执行时间(秒)": 3.42,
                "总执行时间(秒)": 41.04,
            }
        ]


def test_mysql_top_sql_agent_queries_digest_summary_for_alert_window():
    client = RecordingMysqlClient()
    agent = MysqlTopSqlAgent(mysql_client=client, min_running_seconds=10, limit=10)

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert evidence.rows[0].database == "orders"
    assert evidence.rows[0].execution_count == 12
    assert evidence.rows[0].avg_execution_seconds == 3.42
    assert evidence.rows[0].total_execution_seconds == 41.04
    assert evidence.rows[0].sql_text == "select * from orders where status = ?"
    sql, params = client.calls[0]
    assert "performance_schema.events_statements_summary_by_digest" in sql
    assert "LAST_SEEN > %s" in sql
    assert "LAST_SEEN < %s" in sql
    assert "ORDER BY AVG_TIMER_WAIT DESC" in sql
    assert params == ["2026-04-30 14:30:00", "2026-04-30 15:00:00", 10]


class RecordingPrometheusClient:
    def __init__(self):
        self.calls = []

    def range_query(self, *, query: str, start: str, end: str, step: str):
        self.calls.append(
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            }
        )
        value = str(91.2 + len(self.calls))
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"ip": "10.186.17.54"},
                        "values": [
                            [1777528800, value],
                            [1777528860, value],
                        ],
                    }
                ]
            },
        }


def test_prometheus_metric_agent_builds_cpu_range_query_and_parses_values():
    client = RecordingPrometheusClient()
    agent = PrometheusMetricAgent(client=client, step_seconds=60)

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert [metric.metric_name for metric in evidence.metrics] == [
        "cpu_usage",
        "active_threads",
        "slow_sql_count",
    ]
    assert [metric.unit for metric in evidence.metrics] == ["%", "count", "count"]
    assert evidence.metrics[0].ip == "10.186.17.54"
    assert [point.value for point in evidence.metrics[0].values] == [92.2, 92.2]
    assert client.calls[0]["query"] == (
        '100 - (avg by(ip) (rate(node_cpu_seconds_total{mode="idle", '
        'ip="10.186.17.55"}[10m])) * 100)'
    )
    assert client.calls[1]["query"] == (
        'ctg_paas_30202624250003{sysCode="database_prod",'
        'tenant_id="100011",ip="10.186.17.54"}'
    )
    assert client.calls[2]["query"] == (
        'increase(mysql_global_status_slow_queries{ip="10.186.17.54"}[1m])'
    )
    assert client.calls[0]["start"] == "2026-04-30T06:00:00Z"
    assert client.calls[0]["end"] == "2026-04-30T07:00:00Z"
    assert client.calls[0]["step"] == "60s"
    assert len(client.calls) == 3


class FailingPrometheusClient:
    def range_query(self, *, query: str, start: str, end: str, step: str):
        raise RuntimeError("mcp unavailable")


def test_prometheus_metric_agent_prefers_mcp_when_available():
    mcp_client = RecordingPrometheusClient()
    http_client = RecordingPrometheusClient()
    agent = PrometheusMetricAgent(
        mcp_client=mcp_client,
        client=http_client,
        step_seconds=60,
    )

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert len(mcp_client.calls) == 3
    assert len(http_client.calls) == 0


def test_prometheus_metric_agent_falls_back_to_http_when_mcp_fails():
    mcp_client = FailingPrometheusClient()
    http_client = RecordingPrometheusClient()
    agent = PrometheusMetricAgent(
        mcp_client=mcp_client,
        client=http_client,
        step_seconds=60,
    )

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert len(http_client.calls) == 3
    assert evidence.metrics[0].metric_name == "cpu_usage"


class _FakeSseResponse:
    def __init__(self, lines: list[str], headers: dict[str, str] | None = None) -> None:
        self._lines = [line.encode("utf-8") for line in lines]
        self._index = 0
        self.headers = headers or {"Content-Type": "text/event-stream"}

    def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""
        value = self._lines[self._index]
        self._index += 1
        return value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class _FakeHttpResponse:
    def __init__(self, body: str, headers: dict[str, str] | None = None) -> None:
        self._body = body.encode("utf-8")
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def test_prometheus_mcp_client_supports_legacy_sse_transport_and_calls_range_tool():
    seen = {"posts": []}

    def fake_opener(request, timeout=0):
        method = request.get_method()
        if method == "GET":
            assert request.full_url == "http://10.186.42.51:8080/sse"
            return _FakeSseResponse(
                [
                    "event: endpoint\n",
                    "data: /messages/?session_id=session-1\n",
                    "\n",
                ]
            )

        payload = (
            json.loads(request.data.decode("utf-8"))
            if getattr(request, "data", None)
            else None
        )
        seen["posts"].append(
            {
                "url": request.full_url,
                "payload": payload,
                "headers": dict(request.header_items()),
                "timeout": timeout,
            }
        )
        method_name = payload.get("method") if isinstance(payload, dict) else None
        if method_name == "initialize":
            return _FakeHttpResponse(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "prometheus-mcp", "version": "1.0"},
                        },
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": "session-1",
                },
            )
        if method_name == "notifications/initialized":
            return _FakeHttpResponse("", headers={"Content-Type": "application/json"})
        if method_name == "tools/list":
            return _FakeHttpResponse(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "tools": [
                                {"name": "execute_query"},
                                {"name": "execute_range_query"},
                            ]
                        },
                    }
                )
            )
        if method_name == "tools/call":
            assert payload["params"]["name"] == "execute_range_query"
            return _FakeHttpResponse(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {
                                            "resultType": "matrix",
                                            "result": [
                                                {
                                                    "metric": {"ip": "10.186.17.54"},
                                                    "values": [
                                                        [1777528800, "91.2"],
                                                        [1777528860, "93.5"],
                                                    ],
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                            "isError": False,
                        },
                    }
                )
            )
        raise AssertionError(f"unexpected method={method_name!r}")

    client = PrometheusMcpClient(
        sse_url="http://10.186.42.51:8080/sse",
        headers={},
        timeout_seconds=50,
        sse_read_timeout_seconds=50,
        opener=fake_opener,
    )

    payload = client.range_query(
        query="up",
        start="2026-04-30T06:00:00Z",
        end="2026-04-30T07:00:00Z",
        step="60s",
    )

    assert payload["status"] == "success"
    assert payload["data"]["result"][0]["metric"]["ip"] == "10.186.17.54"
    assert [point[1] for point in payload["data"]["result"][0]["values"]] == ["91.2", "93.5"]
    assert [item["payload"]["method"] for item in seen["posts"]] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert seen["posts"][-1]["payload"]["params"]["arguments"] == {
        "query": "up",
        "start": "2026-04-30T06:00:00Z",
        "end": "2026-04-30T07:00:00Z",
        "step": "60s",
    }


def test_prometheus_http_client_calls_query_range_api():
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps({"status": "success", "data": {"result": []}}).encode()

    def fake_opener(request, timeout=0):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    client = PrometheusHttpClient(
        base_url="http://prometheus.example",
        opener=fake_opener,
        timeout_seconds=7,
    )

    payload = client.range_query(
        query="up",
        start="2026-04-30T06:00:00Z",
        end="2026-04-30T07:00:00Z",
        step="60s",
    )

    parsed = urlparse(seen["url"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/api/v1/query_range"
    assert query["query"] == ["up"]
    assert query["start"] == ["2026-04-30T06:00:00Z"]
    assert query["end"] == ["2026-04-30T07:00:00Z"]
    assert query["step"] == ["60s"]
    assert seen["timeout"] == 7
    assert payload["status"] == "success"
