from __future__ import annotations

import os
from pathlib import Path

from chatdba.alarm_binlog.models import (
    AlarmBinlogConfig,
    AlarmFilterSettings,
    AlarmMysqlSettings,
    AlarmRuntimeSettings,
    AlarmWebhookSettings,
)


class AlarmBinlogConfigError(ValueError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AlarmBinlogConfigError(f"Missing required setting: {name}")
    return value


def _parse_event_codes(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values:
        raise AlarmBinlogConfigError("ALARM_FILTER_EVENT_CODES must not be empty")
    return values


def load_alarm_binlog_config() -> AlarmBinlogConfig:
    return AlarmBinlogConfig(
        mysql=AlarmMysqlSettings(
            host=_require("ALARM_MYSQL_HOST"),
            port=int(_require("ALARM_MYSQL_PORT")),
            user=_require("ALARM_MYSQL_USER"),
            password=_require("ALARM_MYSQL_PASSWORD"),
            database=_require("ALARM_MYSQL_DATABASE"),
            table=_require("ALARM_MYSQL_TABLE"),
            server_id=int(_require("ALARM_MYSQL_SERVER_ID")),
        ),
        filter=AlarmFilterSettings(
            sys_code=_require("ALARM_FILTER_SYS_CODE"),
            event_codes=_parse_event_codes(_require("ALARM_FILTER_EVENT_CODES")),
        ),
        webhook=AlarmWebhookSettings(
            url=_require("ALARM_DINGTALK_WEBHOOK_URL"),
            timeout_seconds=float(os.getenv("ALARM_DINGTALK_TIMEOUT_SECONDS", "10")),
        ),
        runtime=AlarmRuntimeSettings(
            checkpoint_file=Path(_require("ALARM_CHECKPOINT_FILE")),
            retry_max_attempts=int(os.getenv("ALARM_RETRY_MAX_ATTEMPTS", "3")),
            retry_initial_delay_seconds=float(
                os.getenv("ALARM_RETRY_INITIAL_DELAY_SECONDS", "1")
            ),
            retry_max_delay_seconds=float(
                os.getenv("ALARM_RETRY_MAX_DELAY_SECONDS", "30")
            ),
            log_level=os.getenv("ALARM_LOG_LEVEL", "INFO"),
        ),
    )
