from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.service import OptimizationTaskExecution
from chatdba.worker.run_fault_diagnosis import (
    MetricAgent,
    ProgressSink,
    TopSqlAgent,
    run_fault_diagnosis_task,
)


FaultDiagnosisTaskRunner = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class FaultDiagnosisRequest:
    task_id: str
    input_text: str
    dingtalk: DingTalkContext | None = None


class FaultDiagnosisTaskService:
    def __init__(
        self,
        *,
        top_sql_agent: TopSqlAgent | None = None,
        metric_agent: MetricAgent | None = None,
        qwen_gateway=None,
        task_runner: FaultDiagnosisTaskRunner = run_fault_diagnosis_task,
        task_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._top_sql_agent = top_sql_agent
        self._metric_agent = metric_agent
        self._qwen_gateway = qwen_gateway
        self._task_runner = task_runner
        self._task_id_factory = task_id_factory or (lambda: str(uuid4()))

    def run_diagnosis(
        self,
        *,
        input_text: str,
        dingtalk_context: DingTalkContext,
        progress_sink: ProgressSink | None = None,
    ) -> OptimizationTaskExecution:
        request = FaultDiagnosisRequest(
            task_id=self._task_id_factory(),
            input_text=input_text,
            dingtalk=dingtalk_context,
        )
        task_payload = {
            "task_id": request.task_id,
            "input_text": request.input_text,
            "dingtalk": request.dingtalk.model_dump(mode="python")
            if request.dingtalk
            else None,
        }
        try:
            result = self._task_runner(
                task_payload,
                top_sql_agent=self._top_sql_agent,
                metric_agent=self._metric_agent,
                qwen_gateway=self._qwen_gateway,
                progress_sink=progress_sink,
            )
        except Exception as exc:
            return OptimizationTaskExecution(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                error=str(exc) or exc.__class__.__name__,
            )

        return OptimizationTaskExecution(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            result=result,
        )
