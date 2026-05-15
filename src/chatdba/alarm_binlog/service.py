from __future__ import annotations

import logging
import time
from uuid import uuid4

import pymysql

from chatdba.alarm_binlog.binlog_worker import (
    extract_inserted_alarm_records,
    fetch_table_columns,
    stream_insert_events,
)
from chatdba.alarm_binlog.models import (
    AlarmBinlogConfig,
    AlarmBinlogRecord,
    AlarmRuntimeSettings,
)
from chatdba.domain.models import DingTalkContext, TaskStatus

LOGGER = logging.getLogger(__name__)


def deliver_alarm(
    *,
    alarm: AlarmBinlogRecord,
    diagnosis_service,
    webhook_sender,
    checkpoint_store,
) -> None:
    execution = diagnosis_service.run_diagnosis(
        input_text=alarm.alarm_content,
        dingtalk_context=_alarm_dingtalk_context(alarm),
        progress_sink=None,
    )
    if execution.status != TaskStatus.COMPLETED or execution.result is None:
        raise RuntimeError(execution.error or "fault diagnosis failed")

    report = execution.result.get("report")
    markdown = getattr(report, "markdown", str(report))
    webhook_sender.send_markdown(
        title=f"ChatDBA 智能诊断报告 #{alarm.main_record_id}",
        markdown=markdown,
    )
    checkpoint_store.save(alarm.main_record_id)


def retry_deliver_alarm(
    *,
    alarm: AlarmBinlogRecord,
    diagnosis_service,
    webhook_sender,
    checkpoint_store,
    runtime: AlarmRuntimeSettings,
) -> None:
    delay = runtime.retry_initial_delay_seconds
    for attempt in range(1, runtime.retry_max_attempts + 1):
        try:
            deliver_alarm(
                alarm=alarm,
                diagnosis_service=diagnosis_service,
                webhook_sender=webhook_sender,
                checkpoint_store=checkpoint_store,
            )
            return
        except Exception:
            LOGGER.warning(
                "alarm diagnosis delivery failed: alarm_id=%s attempt=%s",
                alarm.main_record_id,
                attempt,
                exc_info=True,
            )
            if attempt == runtime.retry_max_attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, runtime.retry_max_delay_seconds)


def anchor_to_latest_id(connection, table: str, checkpoint_store) -> int:
    safe_table = table.replace("`", "``")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COALESCE(MAX(main_record_id), 0) AS latest_id FROM `{safe_table}`"
        )
        row = cursor.fetchone()
    latest_id = int((row or {}).get("latest_id", 0))
    checkpoint_store.save(latest_id)
    return latest_id


def run_alarm_binlog_service(
    *,
    config: AlarmBinlogConfig,
    checkpoint_store,
    diagnosis_service,
    webhook_sender,
) -> None:
    while True:
        connection = pymysql.connect(
            host=config.mysql.host,
            port=config.mysql.port,
            user=config.mysql.user,
            password=config.mysql.password,
            database=config.mysql.database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            checkpoint = anchor_to_latest_id(
                connection,
                config.mysql.table,
                checkpoint_store,
            )
            try:
                column_names = fetch_table_columns(connection, config.mysql.table)
            except Exception:
                LOGGER.exception("failed to read alarm table columns for binlog mapping")
                column_names = ()

            for event in stream_insert_events(config.mysql, config.mysql.table):
                alarms = extract_inserted_alarm_records(
                    {"rows": event.rows},
                    config.filter,
                    checkpoint,
                    column_names=column_names,
                    logger=LOGGER,
                )
                for alarm in alarms:
                    retry_deliver_alarm(
                        alarm=alarm,
                        diagnosis_service=diagnosis_service,
                        webhook_sender=webhook_sender,
                        checkpoint_store=checkpoint_store,
                        runtime=config.runtime,
                    )
                    checkpoint = alarm.main_record_id
        except Exception:
            LOGGER.exception("alarm binlog stream loop failed; restarting")
            time.sleep(config.runtime.retry_initial_delay_seconds)
        finally:
            connection.close()


def _alarm_dingtalk_context(alarm: AlarmBinlogRecord) -> DingTalkContext:
    return DingTalkContext(
        message_id=f"alarm-binlog-{alarm.main_record_id}-{uuid4()}",
        conversation_id="alarm-binlog",
        sender_id="alarm-binlog",
        sender_name="alarm-binlog",
        session_webhook=None,
    )
