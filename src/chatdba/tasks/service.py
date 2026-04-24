from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from chatdba.domain.models import DingTalkContext, SqlOptimizationRequest, TaskStatus
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.worker.run_task import ProgressSink, run_sql_optimization_task


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
    ) -> None:
        self._collector = collector
        self._report_composer = report_composer
        self._task_runner = task_runner
        self._task_id_factory = task_id_factory or (lambda: str(uuid4()))

    def run_sql(
        self,
        *,
        raw_sql: str,
        dingtalk_context: DingTalkContext,
        progress_sink: ProgressSink | None = None,
    ) -> OptimizationTaskExecution:
        request = SqlOptimizationRequest(
            task_id=self._task_id_factory(),
            raw_sql=raw_sql,
            dingtalk=dingtalk_context,
        )
        task_payload = request.model_dump(mode="python")

        try:
            result = self._task_runner(
                task_payload,
                self._collector,
                report_composer=self._report_composer,
                progress_sink=progress_sink,
            )
        except Exception as exc:
            return OptimizationTaskExecution(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
            )

        return OptimizationTaskExecution(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            result=result,
        )
