from chatdba.fault.cmdb import CmdbHostRepository


class RecordingClient:
    def __init__(self):
        self.calls = []

    def query_all(self, sql: str, params=None):
        self.calls.append((sql, params))
        return [
            {
                "management_ip": "10.186.17.54",
                "business_ip": "10.186.17.55",
                "system_name": "订单系统",
            }
        ]


def test_cmdb_host_repository_resolves_business_ip_by_management_ip():
    client = RecordingClient()
    repository = CmdbHostRepository(client=client, table_name="cmd_hosts")

    record = repository.resolve_by_management_ip("10.186.17.54")

    assert record is not None
    assert record.management_ip == "10.186.17.54"
    assert record.business_ip == "10.186.17.55"
    assert record.system_name == "订单系统"
    sql, params = client.calls[0]
    assert "FROM cmd_hosts" in sql
    assert "WHERE management_ip = %s" in sql
    assert params == ["10.186.17.54"]
