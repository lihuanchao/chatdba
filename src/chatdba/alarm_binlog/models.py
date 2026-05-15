from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AlarmMysqlSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    table: str
    server_id: int


@dataclass(frozen=True)
class AlarmFilterSettings:
    sys_code: str
    event_codes: tuple[str, ...]


@dataclass(frozen=True)
class AlarmWebhookSettings:
    url: str
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class AlarmRuntimeSettings:
    checkpoint_file: Path
    retry_max_attempts: int = 3
    retry_initial_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 30.0
    log_level: str = "INFO"


@dataclass(frozen=True)
class AlarmBinlogConfig:
    mysql: AlarmMysqlSettings
    filter: AlarmFilterSettings
    webhook: AlarmWebhookSettings
    runtime: AlarmRuntimeSettings


@dataclass(frozen=True)
class AlarmBinlogRecord:
    main_record_id: int
    alarm_content: str
    sys_code: str
    event_code: str

    def matches(self, settings: AlarmFilterSettings) -> bool:
        return self.sys_code == settings.sys_code and self.event_code in settings.event_codes
