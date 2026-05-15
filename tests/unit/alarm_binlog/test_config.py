from pathlib import Path

import pytest

from chatdba.alarm_binlog.config import AlarmBinlogConfigError, load_alarm_binlog_config


REQUIRED_ALARM_ENV_NAMES = (
    "ALARM_MYSQL_HOST",
    "ALARM_MYSQL_PORT",
    "ALARM_MYSQL_USER",
    "ALARM_MYSQL_PASSWORD",
    "ALARM_MYSQL_DATABASE",
    "ALARM_MYSQL_TABLE",
    "ALARM_MYSQL_SERVER_ID",
    "ALARM_FILTER_SYS_CODE",
    "ALARM_FILTER_EVENT_CODES",
    "ALARM_CHECKPOINT_FILE",
    "ALARM_DINGTALK_WEBHOOK_URL",
)


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

    config = load_alarm_binlog_config(env_files=())

    assert config.mysql.host == "127.0.0.1"
    assert config.mysql.port == 3306
    assert config.mysql.table == "aps_alarm_record"
    assert config.filter.sys_code == "database_prod"
    assert config.filter.event_codes == ("1222", "1654")
    assert config.runtime.checkpoint_file == tmp_path / "checkpoint.json"
    assert config.runtime.retry_max_attempts == 3
    assert config.webhook.url.startswith("https://oapi.dingtalk.com")


def test_load_alarm_binlog_config_reads_values_from_dotenv_file(
    monkeypatch, tmp_path: Path
):
    for name in REQUIRED_ALARM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "ALARM_MYSQL_HOST=10.0.0.12",
                "ALARM_MYSQL_PORT=3307",
                "ALARM_MYSQL_USER=alarm_dotenv_user",
                "ALARM_MYSQL_PASSWORD=dotenv_secret",
                "ALARM_MYSQL_DATABASE=syalarm_new",
                "ALARM_MYSQL_TABLE=aps_alarm_record",
                "ALARM_MYSQL_SERVER_ID=6011",
                "ALARM_FILTER_SYS_CODE=database_prod",
                "ALARM_FILTER_EVENT_CODES=1222,1654",
                f"ALARM_CHECKPOINT_FILE={tmp_path / 'checkpoint.json'}",
                "ALARM_DINGTALK_WEBHOOK_URL=https://example.test/robot",
            )
        ),
        encoding="utf-8",
    )

    config = load_alarm_binlog_config(env_files=(env_file,))

    assert config.mysql.host == "10.0.0.12"
    assert config.mysql.port == 3307
    assert config.mysql.user == "alarm_dotenv_user"
    assert config.filter.event_codes == ("1222", "1654")
    assert config.runtime.checkpoint_file == tmp_path / "checkpoint.json"
    assert config.webhook.url == "https://example.test/robot"


def test_load_alarm_binlog_config_rejects_missing_required_value(monkeypatch):
    monkeypatch.delenv("ALARM_MYSQL_HOST", raising=False)

    with pytest.raises(AlarmBinlogConfigError):
        load_alarm_binlog_config(env_files=())
