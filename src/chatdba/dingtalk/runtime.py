from dataclasses import dataclass
from typing import Any

from chatdba.dingtalk.handler import DingTalkSqlOptimizationHandler
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkBundle,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.dingtalk.sender import DingTalkSessionWebhookSender
from chatdba.tasks.service import OptimizationTaskService


class UnsupportedMysqlCollector:
    def collect(self, sql: str, tables: list[object]):
        raise RuntimeError(
            "MySQL runtime collector is not configured for the DingTalk runtime yet."
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
    runtime_collector = collector or UnsupportedMysqlCollector()
    runtime_sender = sender or DingTalkSessionWebhookSender()

    responder = DingTalkResponder(runtime_sender)
    task_service = OptimizationTaskService(collector=runtime_collector)
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
