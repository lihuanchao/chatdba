from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chatdba.cases.pgvector_retriever import PgVectorCaseRetriever
from chatdba.cases.repository import load_optimization_cases
from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.handler import (
    extract_fault_diagnosis_text,
    is_fault_diagnosis_message,
    is_sql_optimization_message,
)
from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.dingtalk.runtime import SqlOnlyCollector
from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.fault.runtime import build_fault_diagnosis_runtime
from chatdba.tasks.fault_service import FaultDiagnosisTaskService
from chatdba.tasks.repository import PostgresTaskRepository
from chatdba.tasks.service import OptimizationTaskExecution, OptimizationTaskService
from chatdba.workflow.report_builder import OptimizationReportComposer

try:
    from chatdba.config.settings import Settings
except Exception:
    Settings = None

STREAM_HEARTBEAT_SECONDS = 8.0
REPORT_STREAM_CHUNK_SIZE = 320
STREAM_EVENT_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
LOGGER = logging.getLogger(__name__)
EMPTY_SQL_MESSAGE = (
    "未识别到 SQL，请输入待优化语句，例如：\n"
    "SQL优化\n"
    "select * from orders where user_id = 100;"
)


class CreateOptimizationTaskRequest(BaseModel):
    raw_sql: str


class CreateOptimizationTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus


class TaskServiceProvider:
    def __init__(
        self,
        factory: Callable[[], OptimizationTaskService],
    ) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._service: OptimizationTaskService | None = None
        self._last_error: str | None = None

    @property
    def ready(self) -> bool:
        return self._service is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get(self) -> OptimizationTaskService:
        if self._service is not None:
            return self._service

        with self._lock:
            if self._service is not None:
                return self._service

            try:
                self._service = self._factory()
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc) or exc.__class__.__name__
                LOGGER.exception("Failed to initialize task service, degrade to SQL-only.")
                self._service = OptimizationTaskService(
                    collector=SqlOnlyCollector(
                        f"自定义能力服务初始化失败，已退化为 SQL-only 分析：{self._last_error}"
                    ),
                    report_composer=OptimizationReportComposer(cases=[]),
                )
            return self._service


def _build_task_service() -> OptimizationTaskService:
    collector = SqlOnlyCollector()
    report_composer = OptimizationReportComposer(cases=[])
    task_repository = None
    qwen_gateway = None
    settings = _safe_load_settings()

    if settings is not None:
        collector = _build_configured_collector(settings)
        qwen_gateway = _build_qwen_gateway(settings)
        report_composer = _build_report_composer(settings, qwen_gateway=qwen_gateway)
        task_repository = _build_task_repository(settings)

    return OptimizationTaskService(
        collector=collector,
        report_composer=report_composer,
        task_repository=task_repository,
        qwen_gateway=qwen_gateway,
    )


def _build_fault_task_service() -> FaultDiagnosisTaskService:
    settings = _safe_load_settings()
    if settings is None:
        return FaultDiagnosisTaskService()

    fault_runtime = build_fault_diagnosis_runtime(settings)
    return FaultDiagnosisTaskService(
        top_sql_agent=fault_runtime.top_sql_agent,
        metric_agent=fault_runtime.metric_agent,
        cmdb_resolver=fault_runtime.cmdb_resolver,
        qwen_gateway=_build_qwen_gateway(settings),
    )


def _safe_load_settings():
    if Settings is None:
        return None
    try:
        return Settings()
    except Exception:
        LOGGER.exception("Failed to load settings, degrade to default SQL-only runtime.")
        return None


def _build_report_composer(
    settings,
    *,
    qwen_gateway=None,
) -> OptimizationReportComposer:
    cases = _load_cases_from_settings(settings)
    gateway = qwen_gateway if qwen_gateway is not None else _build_qwen_gateway(settings)
    if gateway is None:
        return OptimizationReportComposer(cases=cases)
    return OptimizationReportComposer(
        cases=cases,
        qwen_gateway=gateway,
        case_retriever=_build_case_retriever(settings, cases, gateway),
    )


def _build_qwen_gateway(settings):
    if not settings.qwen_api_key:
        return None
    try:
        from openai import OpenAI

        from chatdba.models.qwen_gateway import QwenGateway
    except Exception:
        LOGGER.exception("Failed to import Qwen dependencies.")
        return None

    return QwenGateway(
        client=OpenAI(
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key,
        ),
        model=settings.qwen_model,
        embedding_model=getattr(settings, "qwen_embedding_model", None),
    )


def _load_cases_from_settings(settings) -> list:
    try:
        cases = load_optimization_cases(settings.database_url)
    except Exception:
        LOGGER.exception("Failed to load optimization cases, continue without cases.")
        return []
    LOGGER.info("Loaded optimization cases: count=%s", len(cases))
    return cases


def _build_case_retriever(settings, cases, gateway):
    if not cases:
        return None
    database_url = getattr(settings, "database_url", "")
    if not database_url:
        return None
    return PgVectorCaseRetriever(
        cases=cases,
        embedding_gateway=gateway,
        database_url=database_url,
        vector_top_k=int(getattr(settings, "case_retrieval_vector_top_k", 12)),
        candidate_limit=int(getattr(settings, "case_retrieval_candidate_limit", 12)),
    )


def _build_task_repository(settings):
    database_url = getattr(settings, "database_url", "")
    if not database_url:
        return None
    return PostgresTaskRepository(database_url)


def _build_configured_collector(settings):
    if not (
        settings.metadata_mysql_host
        and settings.metadata_mysql_user
        and settings.metadata_mysql_database
    ):
        return SqlOnlyCollector()

    try:
        import pymysql

        from chatdba.db.metadata_router import MetadataRouter, MysqlMetadataRouteRepository
        from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
        from chatdba.db.runtime_mysql import SourceMysqlConnectionFactory, build_metadata_client
    except Exception:
        LOGGER.exception("Failed to import MySQL runtime dependencies.")
        return SqlOnlyCollector(
            "已配置元数据库路由，但缺少运行时 MySQL 依赖，已退化为 SQL-only 分析。"
        )

    connect_fn = getattr(pymysql, "connect", None)
    if not callable(connect_fn):
        return SqlOnlyCollector(
            "PyMySQL.connect 不可用，无法采集源库执行证据。"
        )

    try:
        metadata_client = build_metadata_client(settings)
        router = MetadataRouter(
            MysqlMetadataRouteRepository(
                client=metadata_client,
                route_table=settings.metadata_route_table,
                instance_table=settings.metadata_instance_table,
            )
        )
        return RoutedMysqlEvidenceCollector(
            router=router,
            connection_factory=SourceMysqlConnectionFactory(
                connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
                query_timeout_seconds=settings.mysql_query_timeout_seconds,
                connection_factory=connect_fn,
                cursorclass=getattr(pymysql.cursors, "DictCursor", None),
            ),
        )
    except Exception as exc:
        LOGGER.exception("Failed to initialize metadata router, degrade to SQL-only.")
        return SqlOnlyCollector(
            f"元数据库路由初始化失败：{exc}"
        )


def _extract_sql_from_payload(payload: dict[str, object]) -> str:
    raw_text = _extract_text_payload(payload).strip()
    if not raw_text:
        return ""
    message = DingTalkInboundMessage(
        message_id="stream-request",
        conversation_id="stream-request",
        sender_id="stream-request",
        text=raw_text,
    )
    return extract_sql_from_message(message).strip()


def _extract_text_payload(payload: dict[str, object]) -> str:
    direct_keys = ("raw_sql", "sql", "query", "text", "input", "prompt", "content")
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    body = payload.get("body")
    if isinstance(body, str) and body.strip():
        try:
            body_json = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(body_json, dict):
            extracted = _extract_text_payload(body_json)
            if extracted:
                return extracted

    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, dict):
                extracted = _extract_message_content(item.get("content"))
                if extracted:
                    return extracted

    request_line = payload.get("requestLine")
    if isinstance(request_line, dict):
        uri = request_line.get("uri")
        if isinstance(uri, str) and uri:
            query = parse_qs(urlparse(uri).query)
            for name in ("input", "query", "text"):
                values = query.get(name)
                if values:
                    value = values[0]
                    if value.strip():
                        return value

    return ""


def _extract_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict):
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return ""


def _format_sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_events_for_sql(
    raw_sql: str,
    service_factory: Callable[[], OptimizationTaskService] = _build_task_service,
):
    events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()

    def emit_progress(chunk: str) -> None:
        text = chunk.strip()
        if text:
            events.put(("progress", {"text": text}))

    def run_task() -> None:
        try:
            service = service_factory()
            execution = service.run_sql(
                raw_sql=raw_sql,
                dingtalk_context=DingTalkContext(
                    message_id=f"stream-{uuid4()}",
                    conversation_id="dingtalk-graph-stream",
                    sender_id="dingtalk-graph",
                    session_webhook=None,
                ),
                progress_sink=emit_progress,
            )
            if execution.status == TaskStatus.COMPLETED and execution.result is not None:
                report_text = _render_report_text(execution.result.get("report"))
                for chunk in _iter_markdown_chunks(report_text):
                    events.put(("markdown", {"text": chunk}))
            events.put(("final", _build_final_event(execution)))
        except Exception as exc:
            LOGGER.exception("Unhandled error while processing /v1/stream task.")
            events.put(("error", {"message": str(exc) or exc.__class__.__name__}))
        finally:
            events.put(("done", {}))

    worker = threading.Thread(target=run_task, daemon=True)
    worker.start()

    yield _format_sse("ready", {"status": "accepted"})
    last_heartbeat = time.monotonic()

    while True:
        try:
            event, payload = events.get(timeout=0.5)
        except queue.Empty:
            if time.monotonic() - last_heartbeat >= STREAM_HEARTBEAT_SECONDS:
                yield _format_sse("heartbeat", {"ts": int(time.time())})
                last_heartbeat = time.monotonic()
            continue

        if event == "done":
            yield _format_sse("end", {"status": "completed"})
            return

        yield _format_sse(event, payload)


def _stream_events_for_fault(
    input_text: str,
    service_factory: Callable[[], FaultDiagnosisTaskService] = _build_fault_task_service,
):
    events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()

    def emit_progress(chunk: str) -> None:
        text = chunk.strip()
        if text:
            events.put(("progress", {"text": text}))

    def run_task() -> None:
        try:
            service = service_factory()
            execution = service.run_diagnosis(
                input_text=input_text,
                dingtalk_context=DingTalkContext(
                    message_id=f"stream-{uuid4()}",
                    conversation_id="dingtalk-graph-stream",
                    sender_id="dingtalk-graph",
                    session_webhook=None,
                ),
                progress_sink=emit_progress,
            )
            if execution.status == TaskStatus.COMPLETED and execution.result is not None:
                report_text = _render_report_text(execution.result.get("report"))
                for chunk in _iter_markdown_chunks(report_text):
                    events.put(("markdown", {"text": chunk}))
            events.put(("final", _build_final_event(execution)))
        except Exception as exc:
            LOGGER.exception("Unhandled error while processing /v1/stream fault task.")
            events.put(("error", {"message": str(exc) or exc.__class__.__name__}))
        finally:
            events.put(("done", {}))

    worker = threading.Thread(target=run_task, daemon=True)
    worker.start()

    yield _format_sse("ready", {"status": "accepted"})
    last_heartbeat = time.monotonic()

    while True:
        try:
            event, payload = events.get(timeout=0.5)
        except queue.Empty:
            if time.monotonic() - last_heartbeat >= STREAM_HEARTBEAT_SECONDS:
                yield _format_sse("heartbeat", {"ts": int(time.time())})
                last_heartbeat = time.monotonic()
            continue

        if event == "done":
            yield _format_sse("end", {"status": "completed"})
            return

        yield _format_sse(event, payload)


def _build_final_event(execution: OptimizationTaskExecution) -> dict[str, object]:
    if execution.status == TaskStatus.COMPLETED:
        return {
            "task_id": execution.task_id,
            "status": execution.status.value,
        }
    return {
        "task_id": execution.task_id,
        "status": execution.status.value,
        "error": execution.error or "未知错误",
    }


def _render_report_text(report: object) -> str:
    if report is None:
        return ""
    markdown = getattr(report, "markdown", None)
    if isinstance(markdown, str):
        return markdown
    try:
        return render_report_for_dingtalk(report)  # type: ignore[arg-type]
    except Exception:
        if isinstance(report, dict):
            return json.dumps(report, ensure_ascii=False)
        return str(report)


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


def create_app() -> FastAPI:
    app = FastAPI(title="ChatDBA", version="0.1.0")
    service_provider = TaskServiceProvider(_build_task_service)
    fault_service_provider = TaskServiceProvider(_build_fault_task_service)
    app.state.task_service_provider = service_provider
    app.state.fault_service_provider = fault_service_provider

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    @app.get("/readyz")
    def readyz() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "chatdba",
            "task_service_initialized": service_provider.ready,
            "last_init_error": service_provider.last_error,
        }

    @app.post(
        "/internal/tasks/sql-optimization",
        status_code=202,
        response_model=CreateOptimizationTaskResponse,
    )
    def create_sql_optimization_task(
        request: CreateOptimizationTaskRequest,
    ) -> CreateOptimizationTaskResponse:
        service = service_provider.get()
        task_id = service.create_task_record(raw_sql=request.raw_sql)
        return CreateOptimizationTaskResponse(
            task_id=task_id,
            status=TaskStatus.RECEIVED,
        )

    @app.post("/v1/stream")
    async def dingtalk_graph_stream(request: Request) -> StreamingResponse:
        def error_stream(code: str, message: str):
            yield _format_sse("ready", {"status": "accepted"})
            yield _format_sse("error", {"code": code, "message": message})
            yield _format_sse("end", {"status": "completed"})

        try:
            try:
                payload = await request.json()
            except Exception:
                payload = {}

            if not isinstance(payload, dict):
                payload = {"input": str(payload)}

            raw_text = _extract_text_payload(payload).strip()
            if is_fault_diagnosis_message(raw_text):
                input_text = extract_fault_diagnosis_text(raw_text).strip()
                if not input_text:
                    return StreamingResponse(
                        error_stream(
                            "empty_fault_diagnosis",
                            "未识别到故障诊断信息，请输入告警内容、系统名称或 IP。",
                        ),
                        media_type="text/event-stream",
                        headers=STREAM_EVENT_HEADERS,
                    )
                return StreamingResponse(
                    _stream_events_for_fault(input_text, fault_service_provider.get),
                    media_type="text/event-stream",
                    headers=STREAM_EVENT_HEADERS,
                )

            raw_sql = _extract_sql_from_payload(payload)
            if raw_sql and not is_sql_optimization_message(
                DingTalkInboundMessage(
                    message_id="stream-request",
                    conversation_id="stream-request",
                    sender_id="stream-request",
                    text=raw_text,
                )
            ):
                return StreamingResponse(
                    _stream_events_for_fault(raw_text, fault_service_provider.get),
                    media_type="text/event-stream",
                    headers=STREAM_EVENT_HEADERS,
                )
            if not raw_sql:
                return StreamingResponse(
                    error_stream("empty_sql", EMPTY_SQL_MESSAGE),
                    media_type="text/event-stream",
                    headers=STREAM_EVENT_HEADERS,
                )

            return StreamingResponse(
                _stream_events_for_sql(raw_sql, service_provider.get),
                media_type="text/event-stream",
                headers=STREAM_EVENT_HEADERS,
            )
        except Exception as exc:
            LOGGER.exception("Failed before opening /v1/stream response.")
            return StreamingResponse(
                error_stream(
                    "stream_init_failed",
                    str(exc) or exc.__class__.__name__,
                ),
                media_type="text/event-stream",
                headers=STREAM_EVENT_HEADERS,
            )

    return app


app = create_app()
