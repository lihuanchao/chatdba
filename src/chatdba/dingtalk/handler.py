from dataclasses import dataclass, field
import re
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.dingtalk.responder import DingTalkResponder, DingTalkSendResult
from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.db.route_errors import (
    AMBIGUOUS_TABLE_MARKER,
    MULTI_TABLE_SCHEMA_MARKER,
    is_route_resolution_blocker,
)
from chatdba.sql.schema_qualification import (
    extract_schema_name_reply,
    unqualified_table_names,
    qualify_unqualified_tables,
)
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
FAULT_DIAGNOSIS_USAGE_MESSAGE = (
    "请发送需要诊断的故障或告警信息，例如：\n"
    "故障诊断\n"
    "系统名称：订单系统\n"
    "管理IP：10.186.17.54\n"
    "时间：最近1小时 CPU 高"
)
FAULT_DIAGNOSIS_STARTED_MESSAGE = (
    "## 数据库故障诊断任务已接收\n"
    "正在获取 TopSQL 和监控指标，请稍候...\n\n"
)
FAULT_DIAGNOSIS_FAILED_MESSAGE_PREFIX = "数据库故障诊断任务失败："
REPORT_STREAM_CHUNK_SIZE = 320
FAULT_DIAGNOSIS_PREFIXES = ("故障诊断", "故障分析", "数据库诊断", "诊断")
SCHEMA_REQUIRED_MESSAGE_TEMPLATE = (
    "检测到表名在多个数据库或实例中重复，请补充数据库库名后继续分析。\n\n"
    "重复表名：{tables}\n"
    "请直接回复库名，例如：`shop`，系统会结合上一条 SQL 继续分析。"
)


class OptimizationTaskServiceProtocol(Protocol):
    def run_sql(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext,
        progress_sink: ProgressSink | None = None,
    ) -> OptimizationTaskExecution:
        pass


class FaultDiagnosisTaskServiceProtocol(Protocol):
    def run_diagnosis(
        self,
        *,
        input_text: str,
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


@dataclass(frozen=True)
class PendingSqlSchemaRequest:
    raw_sql: str
    table_names: list[str]


class InMemoryPendingSqlSchemaStore:
    def __init__(self) -> None:
        self._requests: dict[str, PendingSqlSchemaRequest] = {}

    def get(self, conversation_id: str) -> PendingSqlSchemaRequest | None:
        return self._requests.get(conversation_id)

    def put(
        self,
        conversation_id: str,
        request: PendingSqlSchemaRequest,
    ) -> None:
        self._requests[conversation_id] = request

    def clear(self, conversation_id: str) -> None:
        self._requests.pop(conversation_id, None)


class DingTalkSqlOptimizationHandler:
    def __init__(
        self,
        *,
        task_service: OptimizationTaskServiceProtocol,
        responder: DingTalkResponder,
        stream_interval_ms: int,
        pending_schema_store: InMemoryPendingSqlSchemaStore | None = None,
    ) -> None:
        self._task_service = task_service
        self._responder = responder
        self._stream_interval_ms = stream_interval_ms
        self._pending_schema_store = (
            pending_schema_store or InMemoryPendingSqlSchemaStore()
        )

    def has_pending_schema_request(self, conversation_id: str) -> bool:
        return self._pending_schema_store.get(conversation_id) is not None

    def handle(self, message: DingTalkInboundMessage) -> DingTalkHandleResult:
        send_results: list[DingTalkSendResult] = []
        raw_sql = extract_sql_from_message(message).strip()
        pending = self._pending_schema_store.get(message.conversation_id)
        schema_name = extract_schema_name_reply(raw_sql) if pending else None
        if pending and schema_name:
            raw_sql = qualify_unqualified_tables(
                pending.raw_sql,
                schema_name=schema_name,
                table_names=pending.table_names,
            )
            self._pending_schema_store.clear(message.conversation_id)

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
            ambiguous_tables = _ambiguous_table_names_from_text(execution.error or "")
            if ambiguous_tables:
                table_names = _table_names_for_pending_schema(
                    raw_sql,
                    ambiguous_tables,
                )
                self._pending_schema_store.put(
                    message.conversation_id,
                    PendingSqlSchemaRequest(
                        raw_sql=raw_sql,
                        table_names=table_names,
                    ),
                )
                bridge.finish()
                send_results.extend(bridge.send_results)
                send_results.append(
                    self._responder.reply_text(
                        message,
                        SCHEMA_REQUIRED_MESSAGE_TEMPLATE.format(
                            tables=", ".join(table_names),
                        ),
                    )
                )
                finish_result = self._responder.finish_stream(message, failed=False)
                if finish_result is not None:
                    send_results.append(finish_result)
                return DingTalkHandleResult(
                    accepted=False,
                    task_id=execution.task_id,
                    status=TaskStatus.FAILED,
                    error="需要补充数据库库名",
                    send_results=send_results,
                )
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
        ambiguous_tables = _ambiguous_table_names_from_report(report)
        if ambiguous_tables:
            table_names = _table_names_for_pending_schema(
                raw_sql,
                ambiguous_tables,
            )
            self._pending_schema_store.put(
                message.conversation_id,
                PendingSqlSchemaRequest(
                    raw_sql=raw_sql,
                    table_names=table_names,
                ),
            )
            bridge.finish()
            send_results.extend(bridge.send_results)
            send_results.append(
                self._responder.reply_text(
                    message,
                    SCHEMA_REQUIRED_MESSAGE_TEMPLATE.format(
                        tables=", ".join(table_names),
                    ),
                )
            )
            finish_result = self._responder.finish_stream(message, failed=False)
            if finish_result is not None:
                send_results.append(finish_result)
            return DingTalkHandleResult(
                accepted=False,
                task_id=execution.task_id,
                status=TaskStatus.FAILED,
                error="需要补充数据库库名",
                send_results=send_results,
            )

        self._pending_schema_store.clear(message.conversation_id)
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


class DingTalkFaultDiagnosisHandler:
    def __init__(
        self,
        *,
        task_service: FaultDiagnosisTaskServiceProtocol,
        responder: DingTalkResponder,
        stream_interval_ms: int,
    ) -> None:
        self._task_service = task_service
        self._responder = responder
        self._stream_interval_ms = stream_interval_ms

    def handle(self, message: DingTalkInboundMessage) -> DingTalkHandleResult:
        input_text = extract_fault_diagnosis_text(message.text).strip()
        if not input_text:
            send_result = self._responder.reply_text(
                message,
                FAULT_DIAGNOSIS_USAGE_MESSAGE,
            )
            return DingTalkHandleResult(
                accepted=False,
                status=TaskStatus.FAILED,
                error="未识别到故障诊断信息",
                send_results=[send_result],
            )

        return _run_streaming_task(
            message=message,
            responder=self._responder,
            stream_interval_ms=self._stream_interval_ms,
            started_message=FAULT_DIAGNOSIS_STARTED_MESSAGE,
            failed_message_prefix=FAULT_DIAGNOSIS_FAILED_MESSAGE_PREFIX,
            run_task=lambda dingtalk_context, progress_sink: self._task_service.run_diagnosis(
                input_text=input_text,
                dingtalk_context=dingtalk_context,
                progress_sink=progress_sink,
            ),
            render_report=lambda report: getattr(report, "markdown", str(report)),
        )


class DingTalkChatDBAHandler:
    def __init__(
        self,
        *,
        sql_handler: DingTalkSqlOptimizationHandler,
        fault_handler: DingTalkFaultDiagnosisHandler,
    ) -> None:
        self._sql_handler = sql_handler
        self._fault_handler = fault_handler

    def handle(self, message: DingTalkInboundMessage) -> DingTalkHandleResult:
        if self._sql_handler.has_pending_schema_request(message.conversation_id):
            return self._sql_handler.handle(message)
        if is_fault_diagnosis_message(message.text):
            return self._fault_handler.handle(message)
        if is_sql_optimization_message(message):
            return self._sql_handler.handle(message)
        return self._fault_handler.handle(message)


def is_fault_diagnosis_message(text: str) -> bool:
    normalized = _strip_at_user(text).strip()
    return any(
        normalized.lower().startswith(prefix.lower())
        for prefix in FAULT_DIAGNOSIS_PREFIXES
    )


def is_sql_optimization_message(message: DingTalkInboundMessage) -> bool:
    raw_sql = extract_sql_from_message(message).strip()
    return bool(re.match(r"(?is)^select\b", raw_sql))


def extract_fault_diagnosis_text(text: str) -> str:
    normalized = _strip_at_user(text).strip()
    for prefix in FAULT_DIAGNOSIS_PREFIXES:
        if normalized.lower().startswith(prefix.lower()):
            return normalized[len(prefix) :].strip()
    return normalized


def _run_streaming_task(
    *,
    message: DingTalkInboundMessage,
    responder: DingTalkResponder,
    stream_interval_ms: int,
    started_message: str,
    failed_message_prefix: str,
    run_task,
    render_report,
) -> DingTalkHandleResult:
    send_results: list[DingTalkSendResult] = []
    send_results.append(responder.reply_text(message, started_message))
    bridge = StreamingProgressBridge(
        responder=responder,
        message=message,
        interval_ms=stream_interval_ms,
    )
    dingtalk_context = DingTalkContext(
        message_id=message.message_id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        session_webhook=message.session_webhook,
    )

    try:
        execution = run_task(dingtalk_context, bridge.emit)
    except Exception as exc:
        bridge.finish()
        send_results.extend(bridge.send_results)
        error = _safe_error_message(exc)
        send_results.append(
            responder.reply_text(
                message,
                f"{failed_message_prefix}{error}",
            )
        )
        finish_result = responder.finish_stream(message, failed=True)
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
            responder.reply_text(
                message,
                f"{failed_message_prefix}{error}",
            )
        )
        finish_result = responder.finish_stream(message, failed=True)
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
    markdown_report = render_report(report)
    for chunk in _iter_markdown_chunks(markdown_report):
        bridge.emit_now(chunk)
    bridge.finish()
    send_results.extend(bridge.send_results)
    finish_result = responder.finish_stream(message, failed=False)
    if finish_result is not None:
        send_results.append(finish_result)
    return DingTalkHandleResult(
        accepted=True,
        task_id=execution.task_id,
        status=TaskStatus.COMPLETED,
        send_results=send_results,
    )


def _strip_at_user(text: str) -> str:
    return re.sub(r"^\s*@\S+\s+", "", text, count=1).strip()


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


def _ambiguous_table_names_from_report(report: object) -> list[str]:
    texts: list[str] = []
    for field_name in ("limitations", "collection_errors"):
        value = getattr(report, field_name, None)
        if isinstance(value, list):
            texts.extend(str(item) for item in value)
    return _ambiguous_table_names_from_text("\n".join(texts))


def _ambiguous_table_names_from_text(text: str) -> list[str]:
    table_names: list[str] = []
    if not is_route_resolution_blocker(text):
        return table_names
    markers = (AMBIGUOUS_TABLE_MARKER, MULTI_TABLE_SCHEMA_MARKER)
    if not any(marker in text for marker in markers):
        return ["相关表"]

    for marker in markers:
        for part in text.split(marker)[1:]:
            names_text = part.splitlines()[0]
            for name in re.split(r"[,，、\s]+", names_text.strip()):
                cleaned = name.strip("。.;； ")
                if cleaned and cleaned not in table_names:
                    table_names.append(cleaned)
    return table_names


def _table_names_for_pending_schema(
    raw_sql: str,
    route_error_tables: list[str],
) -> list[str]:
    if route_error_tables != ["相关表"]:
        return route_error_tables
    names = unqualified_table_names(raw_sql)
    return names or route_error_tables
