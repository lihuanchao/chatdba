from chatdba.config.settings import Settings
from chatdba.domain.fault_diagnosis import FaultDiagnosisProfile
from chatdba.fault.runtime import build_fault_diagnosis_runtime


class FakeCursor:
    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchall(self):
        return [
            {
                "数据库名": "orders",
                "SQL语句": "select * from orders",
                "执行次数": 4,
                "平均执行时间(秒)": 2.1,
                "总执行时间(秒)": 8.4,
            }
        ]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class FakePymysqlModule:
    class cursors:
        DictCursor = object()

    def __init__(self):
        self.connection = FakeConnection()
        self.connect_kwargs = None

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return self.connection


def test_settings_expose_fault_diagnosis_data_source_options():
    settings = Settings(
        _env_file=None,
        database_url="postgresql://chatdba:test@localhost/chatdba",
        fault_cmdb_table="cmd_hosts",
        fault_prometheus_base_url="",
        fault_metric_step_seconds=60,
    )

    assert settings.fault_top_sql_port == 8934
    assert settings.fault_top_sql_host == "10.186.0.27"
    assert settings.fault_top_sql_database == "performance_schema"
    assert settings.fault_top_sql_min_running_seconds == 10
    assert settings.fault_top_sql_limit == 10
    assert settings.fault_cmdb_table == "cmd_hosts"
    assert settings.fault_prometheus_mcp_sse_url == "http://10.186.42.51:8080/sse"
    assert settings.fault_prometheus_mcp_headers_json == "{}"
    assert settings.fault_prometheus_mcp_timeout_seconds == 50
    assert settings.fault_prometheus_mcp_sse_read_timeout_seconds == 50
    assert settings.fault_prometheus_base_url == ""
    assert settings.fault_metric_step_seconds == 60


def test_build_fault_runtime_wires_mysql_and_prometheus_agents():
    pymysql_module = FakePymysqlModule()
    settings = Settings(
        database_url="postgresql://chatdba:test@localhost/chatdba",
        fault_top_sql_user="readonly",
        fault_top_sql_password="secret",
        fault_top_sql_port=8934,
        fault_top_sql_database="slowlog",
        fault_prometheus_base_url="http://prometheus.example",
        metadata_mysql_host="metadata.example",
        metadata_mysql_user="metadata",
        metadata_mysql_password="metadata-secret",
        metadata_mysql_database="chatdba",
    )
    runtime = build_fault_diagnosis_runtime(
        settings,
        pymysql_module=pymysql_module,
    )
    profile = FaultDiagnosisProfile(
        input_text="订单系统 CPU 告警",
        system_name="订单系统",
        management_ip="10.187.0.179",
        primary_ip="10.186.17.54",
        alert_time="2026-04-30 15:00:00",
        start_time="2026-04-30 14:00:00",
        end_time="2026-04-30 15:00:00",
        query_background="订单系统数据库故障诊断",
    )

    evidence = runtime.top_sql_agent.analyze(profile)

    assert evidence.status == "success"
    assert evidence.rows[0].sql_text == "select * from orders"
    assert evidence.rows[0].execution_count == 4
    assert pymysql_module.connect_kwargs["host"] == "10.186.0.27"
    assert pymysql_module.connect_kwargs["port"] == 8934
    assert pymysql_module.connect_kwargs["database"] == "slowlog"
    assert pymysql_module.connection.cursor_obj.executed[1] == [
        "2026-04-30 14:30:00",
        "2026-04-30 15:00:00",
        "10.187.0.179",
        10,
    ]
    assert runtime.metric_agent._base_url == "http://prometheus.example"
    assert runtime.metric_agent._mcp_client is not None
    assert runtime.metric_agent._mcp_client._sse_url == "http://10.186.42.51:8080/sse"
    assert runtime.cmdb_resolver is not None
