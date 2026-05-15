from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import OpenAI

from chatdba.alarm_binlog.checkpoint_store import AlarmCheckpointStore
from chatdba.alarm_binlog.config import load_alarm_binlog_config
from chatdba.alarm_binlog.models import AlarmBinlogConfig
from chatdba.alarm_binlog.service import run_alarm_binlog_service
from chatdba.alarm_binlog.webhook_sender import AlarmWebhookSender
from chatdba.config.settings import Settings
from chatdba.fault.runtime import build_fault_diagnosis_runtime
from chatdba.models.qwen_gateway import QwenGateway
from chatdba.tasks.fault_service import FaultDiagnosisTaskService
from chatdba.tasks.repository import PostgresTaskRepository


@dataclass(frozen=True)
class AlarmBinlogComponents:
    checkpoint_store: AlarmCheckpointStore
    diagnosis_service: FaultDiagnosisTaskService
    webhook_sender: AlarmWebhookSender


def build_alarm_binlog_components(config: AlarmBinlogConfig) -> AlarmBinlogComponents:
    settings = Settings()
    fault_runtime = build_fault_diagnosis_runtime(settings)
    qwen_gateway = None
    if settings.qwen_api_key:
        qwen_gateway = QwenGateway(
            client=OpenAI(
                base_url=settings.qwen_base_url,
                api_key=settings.qwen_api_key,
            ),
            model=settings.qwen_model,
            embedding_model=getattr(settings, "qwen_embedding_model", None),
        )
    return AlarmBinlogComponents(
        checkpoint_store=AlarmCheckpointStore(config.runtime.checkpoint_file),
        diagnosis_service=FaultDiagnosisTaskService(
            top_sql_agent=fault_runtime.top_sql_agent,
            metric_agent=fault_runtime.metric_agent,
            cmdb_resolver=fault_runtime.cmdb_resolver,
            qwen_gateway=qwen_gateway,
            task_repository=_build_task_repository(settings),
        ),
        webhook_sender=AlarmWebhookSender(config.webhook),
    )


def _build_task_repository(settings):
    database_url = getattr(settings, "database_url", "")
    if not database_url:
        return None
    return PostgresTaskRepository(database_url)


def main() -> None:
    config = load_alarm_binlog_config()
    logging.basicConfig(
        level=getattr(logging, config.runtime.log_level.upper(), logging.INFO)
    )
    components = build_alarm_binlog_components(config)
    run_alarm_binlog_service(
        config=config,
        checkpoint_store=components.checkpoint_store,
        diagnosis_service=components.diagnosis_service,
        webhook_sender=components.webhook_sender,
    )


if __name__ == "__main__":
    main()
