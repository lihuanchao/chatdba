from collections.abc import Callable
from dataclasses import dataclass
import logging
from uuid import uuid4

from chatdba.domain.models import AgentTokenUsage, DingTalkContext, TaskStatus
from chatdba.tasks.events import ProgressEvent
from chatdba.tasks.repository import TaskRepository
from chatdba.tasks.service import OptimizationTaskExecution
from chatdba.worker.run_fault_diagnosis import (
    CmdbResolver,
    MetricAgent,
    ProgressSink,
    TopSqlAgent,
    run_fault_diagnosis_task,
)


FaultDiagnosisTaskRunner = Callable[..., dict[str, object]]
LOGGER = logging.getLogger(__name__)


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
        cmdb_resolver: CmdbResolver | None = None,
        qwen_gateway=None,
        task_runner: FaultDiagnosisTaskRunner = run_fault_diagnosis_task,
        task_id_factory: Callable[[], str] | None = None,
        task_repository: TaskRepository | None = None,
    ) -> None:
        self._top_sql_agent = top_sql_agent
        self._metric_agent = metric_agent
        self._cmdb_resolver = cmdb_resolver
        self._qwen_gateway = qwen_gateway
        self._task_runner = task_runner
        self._task_id_factory = task_id_factory or (lambda: str(uuid4()))
        self._task_repository = task_repository

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
        self._record_task_received(request)
        task_payload = {
            "task_id": request.task_id,
            "input_text": request.input_text,
            "dingtalk": request.dingtalk.model_dump(mode="python")
            if request.dingtalk
            else None,
        }
        wrapped_progress_sink = self._wrap_progress_sink(
            task_id=request.task_id,
            downstream=progress_sink,
        )
        self._start_usage_collection(task_id=request.task_id)
        try:
            result = self._task_runner(
                task_payload,
                top_sql_agent=self._top_sql_agent,
                metric_agent=self._metric_agent,
                cmdb_resolver=self._cmdb_resolver,
                qwen_gateway=self._qwen_gateway,
                progress_sink=wrapped_progress_sink,
            )
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            self._record_event(
                ProgressEvent(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    message=f"故障诊断任务执行失败：{error}",
                    payload={
                        **_event_payload(
                            task_type="fault_diagnosis",
                            stage="failed",
                            status=TaskStatus.FAILED,
                        ),
                        "error": error,
                    },
                )
            )
            return OptimizationTaskExecution(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                error=error,
            )
        finally:
            self._record_collected_token_usage()

        self._record_event(
            ProgressEvent(
                task_id=request.task_id,
                status=TaskStatus.COMPLETED,
                message="故障诊断任务执行完成",
                payload={
                    **_event_payload(
                        task_type="fault_diagnosis",
                        stage="completed",
                        status=TaskStatus.COMPLETED,
                    ),
                    "result_keys": sorted(str(key) for key in result.keys()),
                    "evidence_diagnostics": _evidence_diagnostics(result),
                },
            )
        )
        return OptimizationTaskExecution(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            result=result,
        )

    def _record_task_received(self, request: FaultDiagnosisRequest) -> None:
        repository = self._task_repository
        if repository is None:
            return
        try:
            repository.create_task(
                request.task_id,
                request.input_text,
                request.dingtalk,
            )
            repository.append_event(
                ProgressEvent(
                    task_id=request.task_id,
                    status=TaskStatus.RECEIVED,
                    message="故障诊断任务已接收",
                    payload={
                        **_event_payload(
                            task_type="fault_diagnosis",
                            stage="received",
                            status=TaskStatus.RECEIVED,
                        ),
                        "input_length": len(request.input_text),
                    },
                )
            )
        except Exception:
            LOGGER.warning("Failed to persist received fault task record.", exc_info=True)

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
            status = _status_for_fault_progress_message(message)
            if status is None:
                return
            self._record_event(
                ProgressEvent(
                    task_id=task_id,
                    status=status,
                    message=message.strip(),
                    payload=_event_payload(
                        task_type="fault_diagnosis",
                        stage=status.value,
                        status=status,
                    ),
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
            LOGGER.warning("Failed to persist fault task event.", exc_info=True)

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
            LOGGER.warning("Failed to start fault usage collection.", exc_info=True)

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
            LOGGER.warning("Failed to finish fault usage collection.", exc_info=True)
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
            LOGGER.warning("Failed to persist fault token usage.", exc_info=True)


def _status_for_fault_progress_message(message: str) -> TaskStatus | None:
    normalized = " ".join(message.strip().split())
    if not normalized:
        return None
    if "正在生成故障诊断报告" in normalized:
        return TaskStatus.GENERATING_REPORT
    if "解析故障信息" in normalized or "TopSQL" in normalized or "监控指标" in normalized:
        return TaskStatus.DIAGNOSING
    return None


def _event_payload(
    *,
    task_type: str,
    stage: str,
    status: TaskStatus,
) -> dict[str, object]:
    return {
        "task_type": task_type,
        "stage": stage,
        "status": status.value,
    }


def _evidence_diagnostics(result: dict[str, object]) -> dict[str, object]:
    profile = _mapping_value(result.get("profile"))
    top_sql = _mapping_value(result.get("top_sql"))
    metrics = _mapping_value(result.get("metrics"))
    return {
        "profile_missing_fields": _list_value(profile.get("missing_fields")),
        "top_sql": _list_value(top_sql.get("diagnostics")),
        "metrics": _list_value(metrics.get("diagnostics")),
        "missing_metrics": _list_value(metrics.get("missing_metrics")),
        "top_sql_error": top_sql.get("error_message"),
        "metric_error": metrics.get("error_message"),
    }


def _mapping_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="python")
        return payload if isinstance(payload, dict) else {}
    return {}


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []
