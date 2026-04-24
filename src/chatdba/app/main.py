from __future__ import annotations

import json
import queue
import threading
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.dingtalk.runtime import SqlOnlyCollector
from chatdba.domain.models import DingTalkContext, TaskStatus
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


def _build_task_service() -> OptimizationTaskService:
    collector = SqlOnlyCollector()
    report_composer = OptimizationReportComposer(cases=[])
    settings = _safe_load_settings()

    if settings is not None:
        collector = _build_configured_collector(settings)
        report_composer = _build_report_composer(settings)

    return OptimizationTaskService(
        collector=collector,
        report_composer=report_composer,
    )


def _safe_load_settings():
    if Settings is None:
        return None
    try:
        return Settings()
    except Exception:
        return None


def _build_report_composer(settings) -> OptimizationReportComposer:
    if not settings.qwen_api_key:
        return OptimizationReportComposer(cases=[])

    try:
        from openai import OpenAI

        from chatdba.models.qwen_gateway import QwenGateway
    except Exception:
        return OptimizationReportComposer(cases=[])

    gateway = QwenGateway(
        client=OpenAI(
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key,
        ),
        model=settings.qwen_model,
    )
    return OptimizationReportComposer(cases=[], qwen_gateway=gateway)


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


def _stream_events_for_sql(raw_sql: str):
    events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()

    def emit_progress(chunk: str) -> None:
        text = chunk.strip()
        if text:
            events.put(("progress", {"text": text}))

    def run_task() -> None:
        try:
            service = _build_task_service()
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    @app.post(
        "/internal/tasks/sql-optimization",
        status_code=202,
        response_model=CreateOptimizationTaskResponse,
    )
    def create_sql_optimization_task(
        request: CreateOptimizationTaskRequest,
    ) -> CreateOptimizationTaskResponse:
        _ = request
        return CreateOptimizationTaskResponse(
            task_id=str(uuid4()),
            status=TaskStatus.RECEIVED,
        )

    @app.post("/v1/stream")
    async def dingtalk_graph_stream(request: Request) -> StreamingResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {"input": str(payload)}

        raw_sql = _extract_sql_from_payload(payload)
        if not raw_sql:
            def empty_sql_stream():
                yield _format_sse("error", {"code": "empty_sql", "message": EMPTY_SQL_MESSAGE})
                yield _format_sse("end", {"status": "completed"})

            return StreamingResponse(
                empty_sql_stream(),
                media_type="text/event-stream",
                headers=STREAM_EVENT_HEADERS,
            )

        return StreamingResponse(
            _stream_events_for_sql(raw_sql),
            media_type="text/event-stream",
            headers=STREAM_EVENT_HEADERS,
        )

    return app


app = create_app()
