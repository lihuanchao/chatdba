from dataclasses import dataclass
import logging
import threading
from typing import Any

from openai import OpenAI

from chatdba.cases.pgvector_retriever import PgVectorCaseRetriever
from chatdba.cases.repository import load_optimization_cases
from chatdba.db.metadata_router import MetadataRouter, MysqlMetadataRouteRepository
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.db.runtime_mysql import SourceMysqlConnectionFactory, build_metadata_client
from chatdba.dingtalk.handler import (
    DingTalkChatDBAHandler,
    DingTalkFaultDiagnosisHandler,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkBundle,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.dingtalk.sender import DingTalkCardStreamingSender, DingTalkSessionWebhookSender
from chatdba.models.qwen_gateway import QwenGateway
from chatdba.fault.runtime import build_fault_diagnosis_runtime
from chatdba.tasks.fault_service import FaultDiagnosisTaskService
from chatdba.tasks.repository import PostgresTaskRepository
from chatdba.tasks.service import OptimizationTaskService
from chatdba.workflow.report_builder import OptimizationReportComposer

LOGGER = logging.getLogger(__name__)


class SqlOnlyCollector:
    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or "当前未配置元数据库路由，系统将退化为 SQL-only 分析。"

    def collect(self, sql: str, tables: list[object]):
        from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus

        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=[self._reason],
        )


@dataclass(frozen=True)
class DingTalkSdkRuntime:
    client: Any
    callback_handler: Any
    app_handler: DingTalkChatDBAHandler
    collector: object
    sender: object

    def start(self) -> None:
        self.client.start_forever()


def build_dingtalk_runtime(
    *,
    settings,
    collector: object | None = None,
    sender: object | None = None,
    sdk_bundle: DingTalkSdkBundle | None = None,
) -> DingTalkSdkRuntime:
    bundle = sdk_bundle or load_dingtalk_stream_sdk()
    runtime_collector = collector or SqlOnlyCollector()
    runtime_sender = sender

    if (
        collector is None
        and settings.metadata_mysql_host
        and settings.metadata_mysql_user
        and settings.metadata_mysql_database
    ):
        try:
            import pymysql
        except ModuleNotFoundError:
            runtime_collector = SqlOnlyCollector(
                "已配置元数据库路由，但缺少 PyMySQL 依赖，请安装 `pip install PyMySQL`。"
            )
        else:
            connect_fn = getattr(pymysql, "connect", None)
            if not callable(connect_fn):
                runtime_collector = SqlOnlyCollector(
                    "PyMySQL.connect 不可用，无法连接源 MySQL 实例。"
                )
            else:
                try:
                    metadata_client = build_metadata_client(settings)
                    router = MetadataRouter(
                        MysqlMetadataRouteRepository(
                            client=metadata_client,
                            route_table=settings.metadata_route_table,
                            instance_table=settings.metadata_instance_table,
                        )
                    )
                    runtime_collector = RoutedMysqlEvidenceCollector(
                        router=router,
                        connection_factory=SourceMysqlConnectionFactory(
                            connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
                            query_timeout_seconds=settings.mysql_query_timeout_seconds,
                            connection_factory=connect_fn,
                            cursorclass=getattr(pymysql.cursors, "DictCursor", None),
                        ),
                    )
                except Exception as exc:
                    runtime_collector = SqlOnlyCollector(
                        f"元数据库路由初始化失败：{exc}"
                    )

    credential = bundle.stream_module.Credential(
        settings.dingtalk_client_id,
        settings.dingtalk_client_secret,
    )
    client = bundle.stream_module.DingTalkStreamClient(credential)

    if runtime_sender is None:
        card_instance_cls = getattr(bundle.stream_module, "AIMarkdownCardInstance", None)
        chatbot_message_cls = getattr(bundle.chatbot_module, "ChatbotMessage", None)
        if card_instance_cls is not None and chatbot_message_cls is not None:
            default_template_id = (
                getattr(settings, "dingtalk_ai_card_template_id", "") or ""
            ).strip()
            card_content_field = (
                getattr(settings, "dingtalk_ai_card_content_field", "content")
                or "content"
            ).strip() or "content"
            runtime_sender = DingTalkCardStreamingSender(
                dingtalk_client=client,
                chatbot_message_cls=chatbot_message_cls,
                card_instance_cls=card_instance_cls,
                default_card_template_id=default_template_id or None,
                ai_card_status_inputing=getattr(
                    getattr(bundle.stream_module, "AICardStatus", None),
                    "INPUTING",
                    None,
                ),
                card_content_field=card_content_field,
            )
            LOGGER.info(
                "DingTalk card sender enabled: default_template_id=%s content_field=%s",
                default_template_id or "<sdk-default>",
                card_content_field,
            )
        else:
            runtime_sender = DingTalkSessionWebhookSender()

    responder = DingTalkResponder(runtime_sender)
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
    cases = _load_cases_from_settings(settings)
    report_composer = OptimizationReportComposer(
        qwen_gateway=qwen_gateway,
        cases=cases,
        case_retriever=_build_case_retriever(settings, cases, qwen_gateway),
    )
    task_service = OptimizationTaskService(
        collector=runtime_collector,
        report_composer=report_composer,
        task_repository=_build_task_repository(settings),
        qwen_gateway=qwen_gateway,
    )
    fault_runtime = build_fault_diagnosis_runtime(settings)
    fault_task_service = FaultDiagnosisTaskService(
        top_sql_agent=fault_runtime.top_sql_agent,
        metric_agent=fault_runtime.metric_agent,
        cmdb_resolver=fault_runtime.cmdb_resolver,
        qwen_gateway=qwen_gateway,
    )
    sql_handler = DingTalkSqlOptimizationHandler(
        task_service=task_service,
        responder=responder,
        stream_interval_ms=settings.stream_update_interval_ms,
    )
    fault_handler = DingTalkFaultDiagnosisHandler(
        task_service=fault_task_service,
        responder=responder,
        stream_interval_ms=settings.stream_update_interval_ms,
    )
    app_handler = DingTalkChatDBAHandler(
        sql_handler=sql_handler,
        fault_handler=fault_handler,
    )
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=bundle,
        adapter=adapter,
    )

    client.register_callback_handler(
        bundle.chatbot_module.ChatbotMessage.TOPIC,
        callback_handler,
    )
    return DingTalkSdkRuntime(
        client=client,
        callback_handler=callback_handler,
        app_handler=app_handler,
        collector=runtime_collector,
        sender=runtime_sender,
    )


def _load_cases_from_settings(settings) -> list:
    try:
        cases = load_optimization_cases(settings.database_url)
    except Exception:
        LOGGER.exception("Failed to load optimization cases, continue without cases.")
        return []
    LOGGER.info("Loaded optimization cases: count=%s", len(cases))
    return cases


def _build_case_retriever(settings, cases, qwen_gateway):
    if qwen_gateway is None or not cases:
        return None
    database_url = getattr(settings, "database_url", "")
    if not database_url:
        return None
    return PgVectorCaseRetriever(
        cases=cases,
        embedding_gateway=qwen_gateway,
        database_url=database_url,
        vector_top_k=int(getattr(settings, "case_retrieval_vector_top_k", 12)),
        candidate_limit=int(getattr(settings, "case_retrieval_candidate_limit", 12)),
    )


def _build_task_repository(settings):
    database_url = getattr(settings, "database_url", "")
    if not database_url:
        return None
    return PostgresTaskRepository(database_url)


def create_sdk_callback_handler(
    *,
    bundle: DingTalkSdkBundle,
    adapter: DingTalkStreamChatbotHandler,
):
    class CallbackHandler(bundle.stream_module.ChatbotHandler):
        async def process(self, callback):
            callback_data = getattr(callback, "data", {}) or {}
            _start_callback_worker(adapter=adapter, callback_data=callback_data)
            return bundle.stream_module.AckMessage.STATUS_OK, "OK"

    return CallbackHandler()


def _start_callback_worker(
    *,
    adapter: DingTalkStreamChatbotHandler,
    callback_data: dict[str, Any],
) -> None:
    message_id = str(callback_data.get("msgId", ""))
    worker = threading.Thread(
        target=_process_callback_worker,
        kwargs={
            "adapter": adapter,
            "callback_data": callback_data,
        },
        daemon=True,
        name=f"chatdba-dingtalk-{message_id or 'callback'}",
    )
    worker.start()


def _process_callback_worker(
    *,
    adapter: DingTalkStreamChatbotHandler,
    callback_data: dict[str, Any],
) -> None:
    try:
        result = adapter.handle_callback_data(callback_data)
    except Exception:
        LOGGER.exception("Failed to process DingTalk callback.")
        return

    _log_callback_result(result, callback_data)


def _log_callback_result(result: object, callback_data: dict[str, Any]) -> None:
    message_id = str(callback_data.get("msgId", ""))
    conversation_id = str(callback_data.get("conversationId", ""))
    has_session_webhook = bool(callback_data.get("sessionWebhook"))
    conversation_type = str(callback_data.get("conversationType", ""))
    message_type = str(callback_data.get("msgtype", ""))
    accepted = getattr(result, "accepted", None)
    status = getattr(getattr(result, "status", None), "value", None)
    LOGGER.info(
        "DingTalk callback handled: message_id=%s conversation_id=%s conversation_type=%s msgtype=%s session_webhook=%s accepted=%s status=%s",
        message_id,
        conversation_id,
        conversation_type,
        message_type,
        has_session_webhook,
        accepted,
        status,
    )

    send_results = getattr(result, "send_results", None)
    if not isinstance(send_results, list):
        return

    for item in send_results:
        if getattr(item, "ok", True):
            continue
        LOGGER.warning(
            "DingTalk reply failed: conversation_id=%s error=%s",
            getattr(item, "conversation_id", ""),
            getattr(item, "error", ""),
        )
