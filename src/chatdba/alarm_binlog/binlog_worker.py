from __future__ import annotations

import logging

try:
    from pymysqlreplication import BinLogStreamReader
    from pymysqlreplication.row_event import WriteRowsEvent
except ImportError:  # pragma: no cover - exercised in environments without optional dep
    BinLogStreamReader = None
    WriteRowsEvent = None

from chatdba.alarm_binlog.models import AlarmBinlogRecord, AlarmFilterSettings

REQUIRED_ALARM_FIELDS = ("main_record_id", "alarm_content", "sys_code", "event_code")


def fetch_table_columns(connection, table: str) -> tuple[str, ...]:
    safe_table = table.replace("`", "``")
    with connection.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM `{safe_table}`")
        rows = cursor.fetchall()
    return tuple(str(row["Field"]) for row in rows)


def extract_inserted_alarm_records(
    event: dict,
    settings: AlarmFilterSettings,
    checkpoint: int,
    *,
    column_names: tuple[str, ...] = (),
    logger: logging.Logger | None = None,
) -> list[AlarmBinlogRecord]:
    alarms: list[AlarmBinlogRecord] = []
    for row in event.get("rows", []):
        values = _resolved_values(row.get("values", {}), column_names)
        try:
            alarm = AlarmBinlogRecord(
                main_record_id=int(_lookup_required_field(values, "main_record_id")),
                alarm_content=str(_lookup_required_field(values, "alarm_content")),
                sys_code=str(_lookup_required_field(values, "sys_code")),
                event_code=str(_lookup_required_field(values, "event_code")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if logger is not None:
                logger.warning(
                    "skip binlog row with missing or invalid fields: %s; available keys=%s",
                    exc,
                    sorted(values.keys()),
                )
            continue
        if alarm.main_record_id <= checkpoint:
            continue
        if alarm.matches(settings):
            alarms.append(alarm)
    return alarms


def stream_insert_events(mysql_settings, only_table: str):
    if BinLogStreamReader is None or WriteRowsEvent is None:
        raise RuntimeError(
            "mysql-replication is required for alarm binlog streaming. "
            "Install project dependencies before starting alarm binlog runtime."
        )
    stream = BinLogStreamReader(
        connection_settings={
            "host": mysql_settings.host,
            "port": mysql_settings.port,
            "user": mysql_settings.user,
            "passwd": mysql_settings.password,
        },
        server_id=mysql_settings.server_id,
        blocking=True,
        only_events=[WriteRowsEvent],
        only_schemas=[mysql_settings.database],
        only_tables=[only_table],
    )
    try:
        for event in stream:
            yield event
    finally:
        stream.close()


def _resolved_values(values: dict, column_names: tuple[str, ...]) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for raw_key, value in values.items():
        key = _decode_key(raw_key)
        key = _resolve_unknown_column(key, column_names)
        resolved[key] = value
    return resolved


def _decode_key(key) -> str:
    if isinstance(key, bytes):
        return key.decode("utf-8", errors="replace")
    return str(key)


def _resolve_unknown_column(key: str, column_names: tuple[str, ...]) -> str:
    if not key.startswith("UNKNOWN_COL"):
        return key
    suffix = key[len("UNKNOWN_COL") :]
    if not suffix.isdigit():
        return key
    index = int(suffix)
    if index >= len(column_names):
        return key
    return column_names[index]


def _lookup_required_field(values: dict[str, object], field_name: str) -> object:
    if field_name in values:
        return values[field_name]
    normalized_target = _normalize_key(field_name)
    for key, value in values.items():
        if _normalize_key(key) == normalized_target:
            return value
    raise KeyError(field_name)


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())
