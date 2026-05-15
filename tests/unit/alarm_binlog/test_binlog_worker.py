from types import SimpleNamespace
from unittest.mock import MagicMock

from chatdba.alarm_binlog.binlog_worker import (
    extract_inserted_alarm_records,
    fetch_table_columns,
    stream_insert_events,
)
from chatdba.alarm_binlog.models import AlarmFilterSettings


def test_extract_inserted_alarm_records_translates_write_rows_event():
    event = {
        "rows": [
            {
                "values": {
                    "main_record_id": 9,
                    "alarm_content": "send",
                    "sys_code": "database_prod",
                    "event_code": "1654",
                }
            },
            {
                "values": {
                    "main_record_id": 10,
                    "alarm_content": "skip",
                    "sys_code": "other",
                    "event_code": "1654",
                }
            },
        ]
    }

    alarms = extract_inserted_alarm_records(
        event,
        AlarmFilterSettings("database_prod", ("1222", "1654")),
        checkpoint=8,
    )

    assert [alarm.main_record_id for alarm in alarms] == [9]


def test_extract_inserted_alarm_records_maps_unknown_columns_using_table_order():
    column_names = tuple(
        "main_record_id"
        if index == 0
        else "alarm_content"
        if index == 16
        else "event_code"
        if index == 22
        else "sys_code"
        if index == 31
        else f"col_{index}"
        for index in range(32)
    )
    event = {
        "rows": [
            {
                "values": {
                    "UNKNOWN_COL0": 12,
                    "UNKNOWN_COL16": "send",
                    "UNKNOWN_COL22": "1654",
                    "UNKNOWN_COL31": "database_prod",
                }
            }
        ]
    }

    alarms = extract_inserted_alarm_records(
        event,
        AlarmFilterSettings("database_prod", ("1222", "1654")),
        checkpoint=8,
        column_names=column_names,
    )

    assert [alarm.main_record_id for alarm in alarms] == [12]


def test_fetch_table_columns_reads_column_order():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"Field": "main_record_id"},
        {"Field": "alarm_content"},
        {"Field": "event_code"},
        {"Field": "sys_code"},
    ]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    columns = fetch_table_columns(connection, "aps_alarm_record")

    assert columns == ("main_record_id", "alarm_content", "event_code", "sys_code")


def test_stream_insert_events_yields_events_from_binlog_stream(monkeypatch):
    emitted = SimpleNamespace(rows=[{"values": {"main_record_id": 11}}])

    class FakeStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield emitted

        def close(self):
            self.closed = True

    fake_stream = FakeStream()

    def fake_reader(**kwargs):
        return fake_stream

    monkeypatch.setattr(
        "chatdba.alarm_binlog.binlog_worker.BinLogStreamReader",
        fake_reader,
    )
    monkeypatch.setattr("chatdba.alarm_binlog.binlog_worker.WriteRowsEvent", object)
    mysql_settings = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="alarm_user",
        password="secret",
        database="syalarm_new",
        server_id=5011,
    )

    events = list(stream_insert_events(mysql_settings, "aps_alarm_record"))

    assert events == [emitted]
    assert fake_stream.closed is True
