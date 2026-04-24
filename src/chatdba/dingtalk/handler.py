from dataclasses import dataclass, field
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.dingtalk.responder import DingTalkResponder, DingTalkSendResult
from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.service import OptimizationTaskExecution
from chatdba.worker.run_task import ProgressSink


SQL_OPTIMIZATION_USAGE_MESSAGE = (
    "请发送需要优化的 SQL，例如：\n"
    "SQL优化\n"
    "select * from orders where user_id = 100;"
)
SQL_OPTIMIZATION_STARTED_MESSAGE = (
    "## SQL优化任务已接收\n"
    "正在分析执行计划和元数据，请稍候...\n\n"
)
SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX = "SQL 优化任务失败："
REPORT_STREAM_CHUNK_SIZE = 320


class OptimizationTaskServiceProtocol(Protocol):
    def run_sql(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext,
        progress_sink: ProgressSink | None = None,
    ) -> OptimizationTaskExecution:
        pass


@dataclass(frozen=True)
class DingTalkHandleResult:
    accepted: bool
    status: TaskStatus
    task_id: str | None = None
    error: str | None = None
    send_results: list[DingTalkSendResult] = field(default_factory=list)


class DingTalkSqlOptimizationHandler:
    def __init__(
        self,
        *,
        task_service: OptimizationTaskServiceProtocol,
        responder: DingTalkResponder,
        stream_interval_ms: int,
    ) -> None:
        self._task_service = task_service
        self._responder = responder
        self._stream_interval_ms = stream_interval_ms

    def handle(self, message: DingTalkInboundMessage) -> DingTalkHandleResult:
        send_results: list[DingTalkSendResult] = []
        raw_sql = extract_sql_from_message(message).strip()

        if not raw_sql:
            send_results.append(
                self._responder.reply_text(message, SQL_OPTIMIZATION_USAGE_MESSAGE)
            )
            return DingTalkHandleResult(
                accepted=False,
                status=TaskStatus.FAILED,
                error="未识别到 SQL",
                send_results=send_results,
            )

        send_results.append(
            self._responder.reply_text(message, SQL_OPTIMIZATION_STARTED_MESSAGE)
        )
        bridge = StreamingProgressBridge(
            responder=self._responder,
            message=message,
            interval_ms=self._stream_interval_ms,
        )
        dingtalk_context = DingTalkContext(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
            session_webhook=message.session_webhook,
        )

        try:
            execution = self._task_service.run_sql(
                raw_sql=raw_sql,
                dingtalk_context=dingtalk_context,
                progress_sink=bridge.emit,
            )
        except Exception as exc:
            bridge.finish()
            send_results.extend(bridge.send_results)
            error = _safe_error_message(exc)
            send_results.append(
                self._responder.reply_text(
                    message,
                    f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}{error}",
                )
            )
            finish_result = self._responder.finish_stream(message, failed=True)
            if finish_result is not None:
                send_results.append(finish_result)
            return DingTalkHandleResult(
                accepted=True,
                status=TaskStatus.FAILED,
                error=error,
                send_results=send_results,
            )

        if execution.status == TaskStatus.FAILED:
            bridge.finish()
            send_results.extend(bridge.send_results)
            error = execution.error or "未知错误"
            send_results.append(
                self._responder.reply_text(
                    message,
                    f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}{error}",
                )
            )
            finish_result = self._responder.finish_stream(message, failed=True)
            if finish_result is not None:
                send_results.append(finish_result)
            return DingTalkHandleResult(
                accepted=True,
                task_id=execution.task_id,
                status=TaskStatus.FAILED,
                error=error,
                send_results=send_results,
            )

        report = execution.result["report"]
        markdown_report = render_report_for_dingtalk(report)
        for chunk in _iter_markdown_chunks(markdown_report):
            bridge.emit_now(chunk)
        bridge.finish()
        send_results.extend(bridge.send_results)
        finish_result = self._responder.finish_stream(message, failed=False)
        if finish_result is not None:
            send_results.append(finish_result)
        return DingTalkHandleResult(
            accepted=True,
            task_id=execution.task_id,
            status=TaskStatus.COMPLETED,
            send_results=send_results,
        )


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _iter_markdown_chunks(markdown: str) -> list[str]:
    text = markdown.strip()
    if not text:
        return []

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > REPORT_STREAM_CHUNK_SIZE and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks
