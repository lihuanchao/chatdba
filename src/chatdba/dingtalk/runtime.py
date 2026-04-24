from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from chatdba.db.metadata_router import MetadataRouter, MysqlMetadataRouteRepository
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.db.runtime_mysql import SourceMysqlConnectionFactory, build_metadata_client
from chatdba.dingtalk.handler import DingTalkSqlOptimizationHandler
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkBundle,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.dingtalk.sender import DingTalkSessionWebhookSender
from chatdba.models.qwen_gateway import QwenGateway
from chatdba.tasks.service import OptimizationTaskService
from chatdba.workflow.report_builder import OptimizationReportComposer


class SqlOnlyCollector:
    def collect(self, sql: str, tables: list[object]):
        from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus

        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=["Metadata routing is not configured for this runtime."],
        )


@dataclass(frozen=True)
class DingTalkSdkRuntime:
    client: Any
    callback_handler: Any
    app_handler: DingTalkSqlOptimizationHandler
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
    runtime_sender = sender or DingTalkSessionWebhookSender()

    if (
        collector is None
        and settings.metadata_mysql_host
        and settings.metadata_mysql_user
        and settings.metadata_mysql_database
    ):
        try:
            import pymysql
        except ModuleNotFoundError:
            pymysql = None

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
                connection_factory=pymysql.connect if pymysql is not None else None,
            ),
        )

    responder = DingTalkResponder(runtime_sender)
    qwen_gateway = None
    if settings.qwen_api_key:
        qwen_gateway = QwenGateway(
            client=OpenAI(
                base_url=settings.qwen_base_url,
                api_key=settings.qwen_api_key,
            ),
            model=settings.qwen_model,
        )
    report_composer = OptimizationReportComposer(
        qwen_gateway=qwen_gateway,
        cases=[],
    )
    task_service = OptimizationTaskService(
        collector=runtime_collector,
        report_composer=report_composer,
    )
    app_handler = DingTalkSqlOptimizationHandler(
        task_service=task_service,
        responder=responder,
        stream_interval_ms=settings.stream_update_interval_ms,
    )
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=bundle,
        adapter=adapter,
    )

    credential = bundle.stream_module.Credential(
        settings.dingtalk_client_id,
        settings.dingtalk_client_secret,
    )
    client = bundle.stream_module.DingTalkStreamClient(credential)
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


def create_sdk_callback_handler(
    *,
    bundle: DingTalkSdkBundle,
    adapter: DingTalkStreamChatbotHandler,
):
    class CallbackHandler(bundle.stream_module.ChatbotHandler):
        async def process(self, callback):
            adapter.handle_callback_data(callback.data)
            return bundle.stream_module.AckMessage.STATUS_OK, "OK"

    return CallbackHandler()
