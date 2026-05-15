from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from chatdba.alarm_binlog.models import (
    AlarmBinlogConfig,
    AlarmFilterSettings,
    AlarmMysqlSettings,
    AlarmRuntimeSettings,
    AlarmWebhookSettings,
)
from chatdba.alarm_binlog.runner import build_alarm_binlog_components


def test_build_alarm_binlog_components_wires_diagnosis_service_and_sender(monkeypatch):
    config = AlarmBinlogConfig(
        mysql=AlarmMysqlSettings(
            host="127.0.0.1",
            port=3306,
            user="alarm_user",
            password="secret",
            database="syalarm_new",
            table="aps_alarm_record",
            server_id=5011,
        ),
        filter=AlarmFilterSettings("database_prod", ("1222",)),
        webhook=AlarmWebhookSettings(url="https://example.test/webhook"),
        runtime=AlarmRuntimeSettings(checkpoint_file=Path("/tmp/checkpoint.json")),
    )
    settings = SimpleNamespace(
        qwen_api_key="key",
        qwen_base_url="https://dashscope.example/v1",
        qwen_model="qwen-plus",
        qwen_fallback_model="qwen-max",
    )
    runtime = SimpleNamespace(
        top_sql_agent="top-sql",
        metric_agent="metric",
        cmdb_resolver="cmdb",
    )

    monkeypatch.setattr(
        "chatdba.alarm_binlog.runner.Settings",
        Mock(return_value=settings),
    )
    monkeypatch.setattr(
        "chatdba.alarm_binlog.runner.build_fault_diagnosis_runtime",
        Mock(return_value=runtime),
    )

    components = build_alarm_binlog_components(config)

    assert components.checkpoint_store.path == Path("/tmp/checkpoint.json")
    assert components.webhook_sender._settings.url == "https://example.test/webhook"
    assert components.diagnosis_service._top_sql_agent == "top-sql"
    assert components.diagnosis_service._metric_agent == "metric"
    assert components.diagnosis_service._cmdb_resolver == "cmdb"
