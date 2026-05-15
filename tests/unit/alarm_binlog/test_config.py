from pathlib import Path

import pytest

from chatdba.alarm_binlog.config import AlarmBinlogConfigError, load_alarm_binlog_config


def test_load_alarm_binlog_config_reads_required_values(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ALARM_MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("ALARM_MYSQL_PORT", "3306")
    monkeypatch.setenv("ALARM_MYSQL_USER", "alarm_user")
    monkeypatch.setenv("ALARM_MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("ALARM_MYSQL_DATABASE", "syalarm_new")
    monkeypatch.setenv("ALARM_MYSQL_TABLE", "aps_alarm_record")
    monkeypatch.setenv("ALARM_MYSQL_SERVER_ID", "5011")
    monkeypatch.setenv("ALARM_FILTER_SYS_CODE", "database_prod")
    monkeypatch.setenv("ALARM_FILTER_EVENT_CODES", "1222,1654")
    monkeypatch.setenv("ALARM_CHECKPOINT_FILE", str(tmp_path / "checkpoint.json"))
    monkeypatch.setenv(
        "ALARM_DINGTALK_WEBHOOK_URL",
        "https://oapi.dingtalk.com/robot/send?access_token=token",
    )

    config = load_alarm_binlog_config()

    assert config.mysql.host == "127.0.0.1"
    assert config.mysql.port == 3306
    assert config.mysql.table == "aps_alarm_record"
    assert config.filter.sys_code == "database_prod"
    assert config.filter.event_codes == ("1222", "1654")
    assert config.runtime.checkpoint_file == tmp_path / "checkpoint.json"
    assert config.runtime.retry_max_attempts == 3
    assert config.webhook.url.startswith("https://oapi.dingtalk.com")


def test_load_alarm_binlog_config_rejects_missing_required_value(monkeypatch):
    monkeypatch.delenv("ALARM_MYSQL_HOST", raising=False)

    with pytest.raises(AlarmBinlogConfigError):
        load_alarm_binlog_config()
