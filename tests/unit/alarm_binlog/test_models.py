from chatdba.alarm_binlog.models import AlarmBinlogRecord, AlarmFilterSettings


def test_alarm_record_matches_expected_filter():
    record = AlarmBinlogRecord(
        main_record_id=12,
        alarm_content="数据库 CPU 高",
        sys_code="database_prod",
        event_code="1222",
    )

    assert record.matches(
        AlarmFilterSettings(sys_code="database_prod", event_codes=("1222", "1654"))
    )


def test_alarm_record_rejects_unexpected_event_code():
    record = AlarmBinlogRecord(
        main_record_id=12,
        alarm_content="数据库 CPU 高",
        sys_code="database_prod",
        event_code="9999",
    )

    assert not record.matches(
        AlarmFilterSettings(sys_code="database_prod", event_codes=("1222", "1654"))
    )
