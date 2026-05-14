import os

import pytest

from chatdba.fault.agents import PrometheusMcpClient


pytestmark = pytest.mark.skipif(
    os.getenv("CHATDBA_RUN_PROMETHEUS_MCP_INTEGRATION") != "1",
    reason="Set CHATDBA_RUN_PROMETHEUS_MCP_INTEGRATION=1 to hit the real Prometheus MCP server.",
)


def test_prometheus_mcp_server_exposes_execute_range_query_and_returns_matrix_payload():
    client = PrometheusMcpClient(
        sse_url=os.getenv(
            "CHATDBA_PROMETHEUS_MCP_SSE_URL",
            "http://10.186.42.51:8080/sse",
        ),
        headers={},
        timeout_seconds=int(os.getenv("CHATDBA_PROMETHEUS_MCP_TIMEOUT_SECONDS", "50")),
        sse_read_timeout_seconds=int(
            os.getenv("CHATDBA_PROMETHEUS_MCP_SSE_READ_TIMEOUT_SECONDS", "50")
        ),
    )

    client._ensure_session()
    tools_result = client._jsonrpc_call("tools/list", {})
    tool_names = {
        item.get("name")
        for item in tools_result.get("tools", [])
        if isinstance(item, dict)
    }

    assert "execute_range_query" in tool_names

    payload = client.range_query(
        query=os.getenv("CHATDBA_PROMETHEUS_MCP_TEST_QUERY", "up"),
        start=os.getenv("CHATDBA_PROMETHEUS_MCP_TEST_START", "2026-04-30T06:00:00Z"),
        end=os.getenv("CHATDBA_PROMETHEUS_MCP_TEST_END", "2026-04-30T06:05:00Z"),
        step=os.getenv("CHATDBA_PROMETHEUS_MCP_TEST_STEP", "60s"),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


    assert payload["status"] == "success"
    assert isinstance(payload["data"]["result"], list)
