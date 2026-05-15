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
    LOGGER.info(
        "alarm diagnosis started: alarm_id=%s sys_code=%s event_code=%s",
        alarm.main_record_id,
        alarm.sys_code,
        alarm.event_code,
    )
    execution = diagnosis_service.run_diagnosis(
        input_text=alarm.alarm_content,
        dingtalk_context=_alarm_dingtalk_context(alarm),
        progress_sink=None,
    )
    if execution.status != TaskStatus.COMPLETED or execution.result is None:
        LOGGER.error(
            "alarm diagnosis failed: alarm_id=%s task_id=%s status=%s error=%s",
            alarm.main_record_id,
            getattr(execution, "task_id", None),
            execution.status,
            execution.error,
        )
        raise RuntimeError(execution.error or "fault diagnosis failed")
    task_id = getattr(execution, "task_id", None)
    LOGGER.info(
        "alarm diagnosis completed: alarm_id=%s task_id=%s",
        alarm.main_record_id,
        task_id,
    )

    report = execution.result.get("report")
    markdown = getattr(report, "markdown", str(report))
    LOGGER.info(
        "alarm webhook delivery started: alarm_id=%s task_id=%s",
        alarm.main_record_id,
        task_id,
    )
    webhook_sender.send_markdown(
        title=f"ChatDBA 智能诊断报告 #{alarm.main_record_id}",
        markdown=markdown,
    )
    checkpoint_store.save(alarm.main_record_id)
    LOGGER.info(
        "alarm webhook delivery completed: alarm_id=%s task_id=%s checkpoint=%s",
        alarm.main_record_id,
        task_id,
        alarm.main_record_id,
    )


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
            LOGGER.info(
                "alarm delivery attempt started: alarm_id=%s attempt=%s max_attempts=%s",
                alarm.main_record_id,
                attempt,
                runtime.retry_max_attempts,
            )
            deliver_alarm(
                alarm=alarm,
                diagnosis_service=diagnosis_service,
                webhook_sender=webhook_sender,
                checkpoint_store=checkpoint_store,
            )
            LOGGER.info(
                "alarm delivery attempt succeeded: alarm_id=%s attempt=%s",
                alarm.main_record_id,
                attempt,
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
    processed_count = 0
    failed_count = 0
    while True:
        LOGGER.info(
            "alarm binlog loop connecting: host=%s port=%s database=%s table=%s server_id=%s",
            config.mysql.host,
            config.mysql.port,
            config.mysql.database,
            config.mysql.table,
            config.mysql.server_id,
        )
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
            LOGGER.info(
                "alarm binlog anchored: table=%s checkpoint=%s processed_count=%s failed_count=%s",
                config.mysql.table,
                checkpoint,
                processed_count,
                failed_count,
            )
            try:
                column_names = fetch_table_columns(connection, config.mysql.table)
                LOGGER.info(
                    "alarm table columns loaded: table=%s column_count=%s",
                    config.mysql.table,
                    len(column_names),
                )
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
                    try:
                        retry_deliver_alarm(
                            alarm=alarm,
                            diagnosis_service=diagnosis_service,
                            webhook_sender=webhook_sender,
                            checkpoint_store=checkpoint_store,
                            runtime=config.runtime,
                        )
                    except Exception:
                        failed_count += 1
                        LOGGER.exception(
                            "alarm delivery exhausted retries: alarm_id=%s processed_count=%s failed_count=%s",
                            alarm.main_record_id,
                            processed_count,
                            failed_count,
                        )
                        raise
                    else:
                        processed_count += 1
                        checkpoint = alarm.main_record_id
                        LOGGER.info(
                            "alarm processed: alarm_id=%s checkpoint=%s processed_count=%s failed_count=%s",
                            alarm.main_record_id,
                            checkpoint,
                            processed_count,
                            failed_count,
                        )
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
