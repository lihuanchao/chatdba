import json
from urllib.parse import parse_qs, urlparse

from chatdba.domain.fault_diagnosis import FaultDiagnosisProfile
from chatdba.fault.agents import MysqlTopSqlAgent, PrometheusHttpClient, PrometheusMetricAgent


def make_profile() -> FaultDiagnosisProfile:
    return FaultDiagnosisProfile(
        input_text="订单系统数据库 CPU 告警",
        system_name="订单系统",
        primary_ip="10.186.17.54",
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
                "db": "orders",
                "running_seconds": 42,
                "SQL_TEXT": "select * from orders where status = 'PAID'",
            }
        ]


def test_mysql_top_sql_agent_runs_fixed_performance_schema_query():
    client = RecordingMysqlClient()
    agent = MysqlTopSqlAgent(mysql_client=client, min_running_seconds=10, limit=10)

    evidence = agent.analyze(make_profile())

    assert evidence.status == "success"
    assert evidence.rows[0].database == "orders"
    assert evidence.rows[0].running_seconds == 42
    assert evidence.rows[0].sql_text == "select * from orders where status = 'PAID'"
    sql, params = client.calls[0]
    assert "performance_schema.threads" in sql
    assert "performance_schema.events_statements_current" in sql
    assert "PROCESSLIST_COMMAND != 'Sleep'" in sql
    assert params == [10, 10]


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
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"ip": "10.186.17.54"},
                        "values": [
                            [1777528800, "91.2"],
                            [1777528860, "93.5"],
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
    assert evidence.metrics[0].metric_name == "cpu_usage"
    assert evidence.metrics[0].ip == "10.186.17.54"
    assert evidence.metrics[0].unit == "%"
    assert [point.value for point in evidence.metrics[0].values] == [91.2, 93.5]
    assert client.calls[0]["query"] == (
        '100 - (avg by(ip) (rate(node_cpu_seconds_total{mode="idle", '
        'ip="10.186.17.54"}[10m])) * 100)'
    )
    assert client.calls[0]["start"] == "2026-04-30T06:00:00Z"
    assert client.calls[0]["end"] == "2026-04-30T07:00:00Z"
    assert client.calls[0]["step"] == "60s"


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
