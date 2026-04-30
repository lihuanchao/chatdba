from chatdba.config.settings import Settings
from chatdba.domain.fault_diagnosis import FaultDiagnosisProfile
from chatdba.fault.runtime import build_fault_diagnosis_runtime


class FakeCursor:
    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchall(self):
        return [
            {
                "db": "orders",
                "running_seconds": 21,
                "SQL_TEXT": "select * from orders",
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
    settings = Settings(database_url="postgresql://chatdba:test@localhost/chatdba")

    assert settings.fault_top_sql_port == 8801
    assert settings.fault_top_sql_database == "performance_schema"
    assert settings.fault_top_sql_min_running_seconds == 10
    assert settings.fault_top_sql_limit == 10
    assert settings.fault_prometheus_base_url == ""
    assert settings.fault_metric_step_seconds == 60


def test_build_fault_runtime_wires_mysql_and_prometheus_agents():
    pymysql_module = FakePymysqlModule()
    settings = Settings(
        database_url="postgresql://chatdba:test@localhost/chatdba",
        fault_top_sql_user="readonly",
        fault_top_sql_password="secret",
        fault_top_sql_port=8801,
        fault_prometheus_base_url="http://prometheus.example",
    )
    runtime = build_fault_diagnosis_runtime(
        settings,
        pymysql_module=pymysql_module,
    )
    profile = FaultDiagnosisProfile(
        input_text="订单系统 CPU 告警",
        system_name="订单系统",
        primary_ip="10.186.17.54",
        start_time="2026-04-30 14:00:00",
        end_time="2026-04-30 15:00:00",
        query_background="订单系统数据库故障诊断",
    )

    evidence = runtime.top_sql_agent.analyze(profile)

    assert evidence.status == "success"
    assert evidence.rows[0].sql_text == "select * from orders"
    assert pymysql_module.connect_kwargs["host"] == "10.186.17.54"
    assert pymysql_module.connect_kwargs["port"] == 8801
    assert pymysql_module.connect_kwargs["database"] == "performance_schema"
    assert runtime.metric_agent._base_url == "http://prometheus.example"
