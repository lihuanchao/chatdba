from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import uuid4

from chatdba.domain.models import (
    AgentTokenUsage,
    DingTalkContext,
    SqlOptimizationRequest,
    TaskStatus,
)
from chatdba.db.route_errors import is_route_resolution_blocker
from chatdba.sql.schema_qualification import split_schema_prefixed_sql
from chatdba.tasks.events import ProgressEvent
from chatdba.tasks.repository import TaskRepository
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.worker.run_task import ProgressSink, run_sql_optimization_task

LOGGER = logging.getLogger(__name__)


class OptimizationTaskRunner(Protocol):
    def __call__(
        self,
        task_payload: dict[str, object],
        collector,
        report_composer: OptimizationReportComposer | None = None,
        progress_sink: ProgressSink | None = None,
    ) -> dict[str, object]:
        pass


class UsageTrackableGateway(Protocol):
    def start_usage_collection(self, *, task_id: str) -> None:
        raise NotImplementedError

    def finish_usage_collection(self) -> list[AgentTokenUsage]:
        raise NotImplementedError


@dataclass(frozen=True)
class OptimizationTaskExecution:
    task_id: str
    status: TaskStatus
    result: dict[str, object] | None = None
    error: str | None = None


class OptimizationTaskService:
    def __init__(
        self,
        *,
        collector,
        report_composer: OptimizationReportComposer | None = None,
        task_runner: OptimizationTaskRunner = run_sql_optimization_task,
        task_id_factory: Callable[[], str] | None = None,
        task_repository: TaskRepository | None = None,
        qwen_gateway: UsageTrackableGateway | None = None,
    ) -> None:
        self._collector = collector
        self._report_composer = report_composer
        self._task_runner = task_runner
        self._task_id_factory = task_id_factory or (lambda: str(uuid4()))
        self._task_repository = task_repository
        self._qwen_gateway = qwen_gateway

    def create_task_record(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None = None,
    ) -> str:
        request = self._build_request(
            raw_sql=raw_sql,
            dingtalk_context=dingtalk_context,
        )
        self._record_task_received(request)
        return request.task_id

    def run_sql(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext,
        progress_sink: ProgressSink | None = None,
    ) -> OptimizationTaskExecution:
        request = self._build_request(
            raw_sql=raw_sql,
            dingtalk_context=dingtalk_context,
        )
        self._record_task_received(request)
        task_payload = request.model_dump(mode="python")
        wrapped_progress_sink = self._wrap_progress_sink(
            task_id=request.task_id,
            downstream=progress_sink,
        )
        self._start_usage_collection(task_id=request.task_id)

        try:
            result = self._task_runner(
                task_payload,
                self._collector,
                report_composer=self._report_composer,
                progress_sink=wrapped_progress_sink,
            )
        except Exception as exc:
            error = str(exc)
            self._record_event(
                ProgressEvent(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    message=f"任务执行失败：{error}",
                    payload={"error": error},
                )
            )
            return OptimizationTaskExecution(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                error=error,
            )
        finally:
            self._record_collected_token_usage()

        self._record_case_retrieval_debug_event(request.task_id)
        missing_schema_error = _missing_schema_error(result)
        if missing_schema_error is not None:
            self._record_event(
                ProgressEvent(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    message=missing_schema_error,
                )
            )
            return OptimizationTaskExecution(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                result=result,
                error=missing_schema_error,
            )
        self._record_event(
            ProgressEvent(
                task_id=request.task_id,
                status=TaskStatus.COMPLETED,
                message="任务执行完成",
            )
        )
        return OptimizationTaskExecution(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            result=result,
        )

    def _build_request(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext | None,
    ) -> SqlOptimizationRequest:
        schema_name, sql = split_schema_prefixed_sql(raw_sql)
        return SqlOptimizationRequest(
            task_id=self._task_id_factory(),
            raw_sql=sql,
            schema_name=schema_name,
            dingtalk=dingtalk_context,
        )

    def _record_task_received(self, request: SqlOptimizationRequest) -> None:
        repository = self._task_repository
        if repository is None:
            return
        try:
            repository.create_task(
                request.task_id,
                request.raw_sql,
                request.dingtalk,
            )
            repository.append_event(
                ProgressEvent(
                    task_id=request.task_id,
                    status=TaskStatus.RECEIVED,
                    message="任务已接收",
                )
            )
        except Exception:
            LOGGER.warning("Failed to persist received task record.", exc_info=True)

    def _wrap_progress_sink(
        self,
        *,
        task_id: str,
        downstream: ProgressSink | None,
    ) -> ProgressSink | None:
        if downstream is None and self._task_repository is None:
            return None

        def emit(message: str) -> None:
            if downstream is not None:
                downstream(message)
            status = _status_for_progress_message(message)
            if status is None:
                return
            self._record_event(
                ProgressEvent(
                    task_id=task_id,
                    status=status,
                    message=message.strip(),
                )
            )

        return emit

    def _record_event(self, event: ProgressEvent) -> None:
        repository = self._task_repository
        if repository is None:
            return
        try:
            repository.append_event(event)
        except Exception:
            LOGGER.warning("Failed to persist task event.", exc_info=True)

    def _record_case_retrieval_debug_event(self, task_id: str) -> None:
        debug = _case_retrieval_debug_payload(self._report_composer)
        if debug is None:
            return
        self._record_event(
            ProgressEvent(
                task_id=task_id,
                status=TaskStatus.RETRIEVING_CASES,
                message="案例检索调试信息",
                payload={"case_retrieval": debug},
            )
        )

    def _start_usage_collection(self, *, task_id: str) -> None:
        gateway = self._qwen_gateway
        if gateway is None:
            return
        start = getattr(gateway, "start_usage_collection", None)
        if not callable(start):
            return
        try:
            start(task_id=task_id)
        except Exception:
            LOGGER.warning("Failed to start usage collection.", exc_info=True)

    def _record_collected_token_usage(self) -> None:
        gateway = self._qwen_gateway
        repository = self._task_repository
        if gateway is None or repository is None:
            return
        finish = getattr(gateway, "finish_usage_collection", None)
        if not callable(finish):
            return
        try:
            usages = finish()
        except Exception:
            LOGGER.warning("Failed to finish usage collection.", exc_info=True)
            return
        for usage in usages:
            self._record_token_usage(usage)

    def _record_token_usage(self, usage: AgentTokenUsage) -> None:
        repository = self._task_repository
        if repository is None:
            return
        try:
            repository.append_token_usage(usage)
        except Exception:
            LOGGER.warning("Failed to persist token usage.", exc_info=True)


def _status_for_progress_message(message: str) -> TaskStatus | None:
    normalized = message.strip()
    mapping = {
        "正在解析 SQL...": TaskStatus.PARSING_SQL,
        "已生成诊断结论...": TaskStatus.DIAGNOSING,
        "已生成优化报告...": TaskStatus.GENERATING_REPORT,
    }
    return mapping.get(normalized)


def _case_retrieval_debug_payload(
    report_composer: OptimizationReportComposer | None,
) -> dict[str, object] | None:
    if report_composer is None:
        return None
    debug = getattr(report_composer, "last_case_retrieval_debug", None)
    if not isinstance(debug, Mapping):
        return None
    return dict(debug)


def _missing_schema_error(result: dict[str, object]) -> str | None:
    evidence = result.get("evidence")
    errors = getattr(evidence, "collection_errors", None)
    if not isinstance(errors, list):
        return None
    for error in errors:
        text = str(error)
        if is_route_resolution_blocker(text):
            return text
    return None
