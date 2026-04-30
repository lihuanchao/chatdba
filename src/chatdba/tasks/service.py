from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import uuid4

from chatdba.domain.models import DingTalkContext, SqlOptimizationRequest, TaskStatus
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
    ) -> None:
        self._collector = collector
        self._report_composer = report_composer
        self._task_runner = task_runner
        self._task_id_factory = task_id_factory or (lambda: str(uuid4()))
        self._task_repository = task_repository

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

        self._record_case_retrieval_debug_event(request.task_id)
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
        return SqlOptimizationRequest(
            task_id=self._task_id_factory(),
            raw_sql=raw_sql,
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
