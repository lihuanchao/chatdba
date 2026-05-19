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
                "SQL语句": "select * from orders where status = ?",
                "执行次数": 12,
                "平均执行时间(秒)": 3.42,
                "总执行时间(秒)": 41.04,
            }
        ]


class EmptyMysqlClient:
    def __init__(self):
        self.calls = []

    def query_all(self, sql: str, params=None):
        self.calls.append((sql, params))
        return []


def test_mysql_top_sql_agent_queries_slow_log_summary_for_alert_window_and_management_ip():
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
    assert "monitor_mysql_slow_query_review_rt a" in sql
    assert "`monitor_mysql_slow_query_review_history_rt` b" in sql
    assert "`db_resource` c" in sql
    assert "b.ts_min >= %s" in sql
    assert "b.ts_max <= %s" in sql
    assert "c.user_id = 100011" in sql
    assert "c.host = %s" in sql
    assert "ORDER BY" in sql
    assert "sum(`b`.`Query_time_sum`) DESC" in sql
    assert params == [
        "2026-04-30 14:30:00",
        "2026-04-30 15:00:00",
        "10.186.17.54",
        10,
    ]


def test_mysql_top_sql_agent_records_no_records_as_observable_evidence_gap():
    client = EmptyMysqlClient()
    agent = MysqlTopSqlAgent(mysql_client=client, limit=10)

    evidence = agent.analyze(make_profile())

    assert evidence.status == "failure"
    assert evidence.rows == []
    assert "慢日志库" in evidence.error_message
    assert "未返回 TopSQL" in evidence.error_message
    assert any("top_sql.no_records" in item for item in evidence.diagnostics)


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


class MissingActiveThreadsPrometheusClient(RecordingPrometheusClient):
    def range_query(self, *, query: str, start: str, end: str, step: str):
        self.calls.append(
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            }
        )
        if "ctg_paas_30202624250003" in query:
            return {"status": "success", "data": {"result": []}}
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"ip": "10.186.17.54"},
                        "values": [[1777528800, "1"]],
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
    ]
    assert [metric.unit for metric in evidence.metrics] == ["%", "count"]
    assert evidence.metrics[0].ip == "10.186.17.54"
    assert [point.value for point in evidence.metrics[0].values] == [92.2, 92.2]
    assert client.calls[0]["query"] == (
        'round(100 * (1 - avg by(ip) (irate(node_cpu_seconds_total{mode="idle",'
        'ip="10.186.17.55"}[5m]))), 0.01)'
    )
    assert client.calls[1]["query"] == (
        'ctg_paas_30202624250003{sysCode="database_prod",'
        'tenant_id="100011",ip="10.186.17.54"}'
    )
    assert client.calls[0]["start"] == "2026-04-30T06:00:00Z"
    assert client.calls[0]["end"] == "2026-04-30T07:00:00Z"
    assert client.calls[0]["step"] == "60s"
    assert len(client.calls) == 2


def test_prometheus_metric_agent_converts_east_8_alert_window_to_utc_range():
    client = RecordingPrometheusClient()
    agent = PrometheusMetricAgent(client=client, step_seconds=60)
    profile = make_profile().model_copy(
        update={
            "alert_time": "2026-05-13 09:45:03",
            "start_time": "2026-05-13 09:15:03",
            "end_time": "2026-05-13 09:45:03",
            "timezone": "Asia/Shanghai",
        }
    )

    agent.analyze(profile)

    assert client.calls[0]["start"] == "2026-05-13T01:15:03Z"
    assert client.calls[0]["end"] == "2026-05-13T01:45:03Z"


def test_prometheus_metric_agent_respects_utc_profile_timezone():
    client = RecordingPrometheusClient()
    agent = PrometheusMetricAgent(client=client, step_seconds=60)
    profile = make_profile().model_copy(
        update={
            "start_time": "2026-05-13 01:15:03",
            "end_time": "2026-05-13 01:45:03",
            "timezone": "UTC",
        }
    )

    agent.analyze(profile)

    assert client.calls[0]["start"] == "2026-05-13T01:15:03Z"
    assert client.calls[0]["end"] == "2026-05-13T01:45:03Z"


def test_prometheus_metric_agent_records_missing_metric_when_query_returns_no_data():
    client = MissingActiveThreadsPrometheusClient()
    agent = PrometheusMetricAgent(client=client, step_seconds=60)

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert [metric.metric_name for metric in evidence.metrics] == [
        "cpu_usage",
    ]
    assert len(evidence.missing_metrics) == 1
    assert "active_threads" in evidence.missing_metrics[0]
    assert "未返回数据" in evidence.missing_metrics[0]
    assert evidence.error_message == evidence.missing_metrics[0]


class FailingPrometheusClient:
    def range_query(self, *, query: str, start: str, end: str, step: str):
        raise RuntimeError("mcp unavailable")


class EmptyPrometheusClient:
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
        return {"status": "success", "data": {"result": []}}


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
    assert len(mcp_client.calls) == 2
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
    assert len(http_client.calls) == 2
    assert evidence.metrics[0].metric_name == "cpu_usage"


def test_prometheus_metric_agent_distinguishes_mcp_failure_http_empty_result():
    mcp_client = FailingPrometheusClient()
    http_client = EmptyPrometheusClient()
    agent = PrometheusMetricAgent(
        mcp_client=mcp_client,
        client=http_client,
        step_seconds=60,
    )

    evidence = agent.analyze(make_profile())

    assert evidence.status == "failure"
    assert "cpu_usage" in evidence.error_message
    assert "MCP 查询失败: mcp unavailable" in evidence.error_message
    assert "HTTP 未返回数据" in evidence.error_message
    assert any("metric.cpu_usage" in item for item in evidence.diagnostics)
    assert any("metric.active_threads" in item for item in evidence.diagnostics)


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


class _FakeStreamingSseResponse:
    def __init__(self) -> None:
        self._lines: list[bytes] = [
            b"event: endpoint\n",
            b"data: /messages/?session_id=session-1\n",
            b"\n",
        ]
        self.headers = {"Content-Type": "text/event-stream"}

    def push_jsonrpc(self, payload: dict) -> None:
        self._lines.extend(
            [
                b"event: message\n",
                f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode("utf-8"),
                b"\n",
            ]
        )

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

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
    assert seen["posts"][0]["url"] == "http://10.186.42.51:8080/messages/?session_id=session-1"
    assert seen["posts"][-1]["payload"]["params"]["arguments"] == {
        "query": "up",
        "start": "2026-04-30T06:00:00Z",
        "end": "2026-04-30T07:00:00Z",
        "step": "60s",
    }


def test_prometheus_mcp_client_reads_jsonrpc_responses_from_legacy_sse_stream():
    sse = _FakeStreamingSseResponse()
    seen = {"posts": []}

    def fake_opener(request, timeout=0):
        if request.get_method() == "GET":
            return sse

        payload = json.loads(request.data.decode("utf-8"))
        seen["posts"].append(payload["method"])
        if payload["method"] == "initialize":
            sse.push_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                    },
                }
            )
        elif payload["method"] == "notifications/initialized":
            pass
        elif payload["method"] == "tools/list":
            sse.push_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "execute_range_query"}]},
                }
            )
        elif payload["method"] == "tools/call":
            sse.push_jsonrpc(
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
                                                "values": [[1777528800, "91.2"]],
                                            }
                                        ],
                                    }
                                ),
                            }
                        ],
                        "isError": False,
                    },
                }
            )
        return _FakeHttpResponse("Accepted", headers={"Content-Type": "text/plain"})

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
    assert payload["data"]["result"][0]["values"] == [[1777528800, "91.2"]]
    assert seen["posts"] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]


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
