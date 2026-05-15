from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from chatdba.alarm_binlog.models import (
    AlarmBinlogConfig,
    AlarmBinlogRecord,
    AlarmFilterSettings,
    AlarmMysqlSettings,
    AlarmRuntimeSettings,
    AlarmWebhookSettings,
)
from chatdba.alarm_binlog.service import (
    anchor_to_latest_id,
    deliver_alarm,
    retry_deliver_alarm,
    run_alarm_binlog_service,
)
from chatdba.domain.models import TaskStatus


def test_deliver_alarm_runs_diagnosis_sends_report_and_updates_checkpoint():
    alarm = AlarmBinlogRecord(
        main_record_id=11,
        alarm_content="数据库 CPU 高",
        sys_code="database_prod",
        event_code="1222",
    )
    report = SimpleNamespace(markdown="### 一、问题简述\nCPU 高")
    diagnosis_service = Mock()
    diagnosis_service.run_diagnosis.return_value = SimpleNamespace(
        status=TaskStatus.COMPLETED,
        result={"report": report},
        error=None,
    )
    sender = Mock()
    store = Mock()

    deliver_alarm(
        alarm=alarm,
        diagnosis_service=diagnosis_service,
        webhook_sender=sender,
        checkpoint_store=store,
    )

    diagnosis_service.run_diagnosis.assert_called_once()
    assert diagnosis_service.run_diagnosis.call_args.kwargs["input_text"] == "数据库 CPU 高"
    sender.send_markdown.assert_called_once_with(
        title="ChatDBA 智能诊断报告 #11",
        markdown="### 一、问题简述\nCPU 高",
    )
    store.save.assert_called_once_with(11)


def test_deliver_alarm_does_not_update_checkpoint_when_diagnosis_fails():
    alarm = AlarmBinlogRecord(11, "数据库 CPU 高", "database_prod", "1222")
    diagnosis_service = Mock()
    diagnosis_service.run_diagnosis.return_value = SimpleNamespace(
        status=TaskStatus.FAILED,
        result=None,
        error="metric unavailable",
    )
    sender = Mock()
    store = Mock()

    with pytest.raises(RuntimeError, match="metric unavailable"):
        deliver_alarm(
            alarm=alarm,
            diagnosis_service=diagnosis_service,
            webhook_sender=sender,
            checkpoint_store=store,
        )

    sender.send_markdown.assert_not_called()
    store.save.assert_not_called()


def test_retry_deliver_alarm_retries_until_success(monkeypatch):
    alarm = AlarmBinlogRecord(15, "数据库 CPU 高", "database_prod", "1222")
    diagnosis_service = Mock()
    webhook_sender = Mock()
    checkpoint_store = Mock()
    calls = {"count": 0}

    def flaky_deliver(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")

    sleep_calls = []
    monkeypatch.setattr("chatdba.alarm_binlog.service.time.sleep", sleep_calls.append)
    monkeypatch.setattr("chatdba.alarm_binlog.service.deliver_alarm", flaky_deliver)

    retry_deliver_alarm(
        alarm=alarm,
        diagnosis_service=diagnosis_service,
        webhook_sender=webhook_sender,
        checkpoint_store=checkpoint_store,
        runtime=AlarmRuntimeSettings(
            checkpoint_file=Path("/tmp/checkpoint.json"),
            retry_max_attempts=2,
            retry_initial_delay_seconds=1,
            retry_max_delay_seconds=10,
        ),
    )

    assert calls["count"] == 2
    assert sleep_calls == [1]


def test_anchor_to_latest_id_saves_current_max_id():
    cursor = Mock()
    cursor.fetchone.return_value = {"latest_id": 27}
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    store = Mock()

    latest = anchor_to_latest_id(connection, "aps_alarm_record", store)

    assert latest == 27
    store.save.assert_called_once_with(27)


def test_run_alarm_binlog_service_processes_live_stream_after_anchor(monkeypatch):
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
        filter=AlarmFilterSettings("database_prod", ("1222", "1654")),
        webhook=AlarmWebhookSettings(url="https://example.test/webhook"),
        runtime=AlarmRuntimeSettings(checkpoint_file=Path("/tmp/checkpoint.json")),
    )
    connection = Mock()
    checkpoint_store = Mock()
    diagnosis_service = Mock()
    webhook_sender = Mock()
    delivered = []

    monkeypatch.setattr(
        "chatdba.alarm_binlog.service.pymysql.connect",
        Mock(return_value=connection),
    )
    monkeypatch.setattr(
        "chatdba.alarm_binlog.service.anchor_to_latest_id",
        Mock(return_value=5),
    )
    monkeypatch.setattr(
        "chatdba.alarm_binlog.service.fetch_table_columns",
        Mock(return_value=()),
    )

    def fake_stream(mysql_settings, only_table):
        yield SimpleNamespace(
            rows=[
                {
                    "values": {
                        "main_record_id": 5,
                        "alarm_content": "old",
                        "sys_code": "database_prod",
                        "event_code": "1222",
                    }
                },
                {
                    "values": {
                        "main_record_id": 6,
                        "alarm_content": "stream",
                        "sys_code": "database_prod",
                        "event_code": "1654",
                    }
                },
            ]
        )
        raise KeyboardInterrupt

    def fake_retry_deliver_alarm(**kwargs):
        delivered.append(kwargs["alarm"].main_record_id)

    monkeypatch.setattr("chatdba.alarm_binlog.service.stream_insert_events", fake_stream)
    monkeypatch.setattr(
        "chatdba.alarm_binlog.service.retry_deliver_alarm",
        fake_retry_deliver_alarm,
    )

    with pytest.raises(KeyboardInterrupt):
        run_alarm_binlog_service(
            config=config,
            checkpoint_store=checkpoint_store,
            diagnosis_service=diagnosis_service,
            webhook_sender=webhook_sender,
        )

    assert delivered == [6]
    connection.close.assert_called_once()
