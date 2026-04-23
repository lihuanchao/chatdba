# DingTalk End-To-End SQL Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working in-process DingTalk SQL optimization loop where an inbound DingTalk message starts an optimization task and receives streamed progress plus a final reply.

**Architecture:** Add a thin DingTalk application layer around the existing worker and LangGraph workflow. Keep DingTalk network sending behind an injectable responder protocol, keep task execution behind an injectable service, and buffer progress chunks through the existing `StreamUpdateBuffer`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, pytest, existing ChatDBA domain models.

---

## File Structure

Create or modify these files:

```text
src/chatdba/dingtalk/responder.py
src/chatdba/dingtalk/progress.py
src/chatdba/dingtalk/handler.py
src/chatdba/dingtalk/stream_runtime.py
src/chatdba/tasks/service.py
tests/unit/test_dingtalk_responder.py
tests/unit/test_streaming_progress_bridge.py
tests/unit/test_optimization_task_service.py
tests/unit/test_dingtalk_handler.py
tests/unit/test_dingtalk_runtime.py
tests/integration/test_dingtalk_e2e_flow.py
README.md
```

Responsibilities:

- `dingtalk.responder`: DingTalk text reply abstraction and send result object.
- `dingtalk.progress`: progress buffer bridge from worker chunks to DingTalk replies.
- `tasks.service`: synchronous in-process task service wrapping `run_sql_optimization_task`.
- `dingtalk.handler`: chat-facing orchestration for SQL extraction, task execution, and replies.
- `dingtalk.stream_runtime`: test/runtime adapter that calls the configured handler and returns its result.

## Task 1: DingTalk Responder

**Files:**
- Create: `src/chatdba/dingtalk/responder.py`
- Test: `tests/unit/test_dingtalk_responder.py`

- [ ] **Step 1: Write the failing responder tests**

Create `tests/unit/test_dingtalk_responder.py`:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.responder import DingTalkResponder


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "session_webhook": session_webhook,
                "text": text,
            }
        )


class FailingSender:
    def send_text(self, *, conversation_id, session_webhook, text):
        raise RuntimeError("network down")


def make_message() -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
    )


def test_responder_sends_text_to_session_webhook():
    sender = RecordingSender()
    responder = DingTalkResponder(sender)

    result = responder.reply_text(make_message(), "hello")

    assert result.ok is True
    assert result.conversation_id == "conv-1"
    assert result.message == "hello"
    assert result.error is None
    assert sender.calls == [
        {
            "conversation_id": "conv-1",
            "session_webhook": "https://example.test/webhook",
            "text": "hello",
        }
    ]


def test_responder_captures_sender_errors():
    responder = DingTalkResponder(FailingSender())

    result = responder.reply_text(make_message(), "hello")

    assert result.ok is False
    assert result.conversation_id == "conv-1"
    assert result.message == "hello"
    assert result.error == "network down"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_responder.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.responder'`.

- [ ] **Step 3: Implement the responder**

Create `src/chatdba/dingtalk/responder.py`:

```python
from dataclasses import dataclass
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage


class DingTalkTextSender(Protocol):
    def send_text(
        self,
        *,
        conversation_id: str,
        session_webhook: str | None,
        text: str,
    ) -> None:
        pass


@dataclass(frozen=True)
class DingTalkSendResult:
    conversation_id: str
    message: str
    ok: bool
    error: str | None = None


class DingTalkResponder:
    def __init__(self, sender: DingTalkTextSender) -> None:
        self._sender = sender

    def reply_text(
        self,
        message: DingTalkInboundMessage,
        text: str,
    ) -> DingTalkSendResult:
        try:
            self._sender.send_text(
                conversation_id=message.conversation_id,
                session_webhook=message.session_webhook,
                text=text,
            )
        except Exception as exc:
            return DingTalkSendResult(
                conversation_id=message.conversation_id,
                message=text,
                ok=False,
                error=str(exc),
            )

        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )
```

- [ ] **Step 4: Run responder tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_responder.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/responder.py tests/unit/test_dingtalk_responder.py
git commit -m "feat: add dingtalk responder abstraction"
```

Expected: commit succeeds.

## Task 2: Streaming Progress Bridge

**Files:**
- Create: `src/chatdba/dingtalk/progress.py`
- Test: `tests/unit/test_streaming_progress_bridge.py`

- [ ] **Step 1: Write the failing progress bridge tests**

Create `tests/unit/test_streaming_progress_bridge.py`:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.responder import DingTalkSendResult


class RecordingResponder:
    def __init__(self):
        self.messages = []

    def reply_text(self, message, text):
        self.messages.append(text)
        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )


def make_message() -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
        session_webhook="https://example.test/webhook",
    )


def test_progress_bridge_flushes_when_interval_elapsed():
    clock_values = iter([0, 500, 1500])
    responder = RecordingResponder()
    bridge = StreamingProgressBridge(
        responder=responder,
        message=make_message(),
        interval_ms=1000,
        clock_ms=lambda: next(clock_values),
    )

    bridge.emit("Parsing SQL\n")
    bridge.emit("Collecting EXPLAIN\n")

    assert responder.messages == ["Parsing SQL\nCollecting EXPLAIN\n"]
    assert [result.message for result in bridge.send_results] == [
        "Parsing SQL\nCollecting EXPLAIN\n"
    ]


def test_progress_bridge_finish_force_flushes_remaining_chunks():
    responder = RecordingResponder()
    bridge = StreamingProgressBridge(
        responder=responder,
        message=make_message(),
        interval_ms=1000,
        clock_ms=lambda: 0,
    )

    bridge.emit("Generated diagnostic findings\n")
    bridge.finish()

    assert responder.messages == ["Generated diagnostic findings\n"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_streaming_progress_bridge.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.progress'`.

- [ ] **Step 3: Implement the progress bridge**

Create `src/chatdba/dingtalk/progress.py`:

```python
from collections.abc import Callable

from chatdba.dingtalk.channel import DingTalkInboundMessage, StreamUpdateBuffer
from chatdba.dingtalk.responder import DingTalkResponder, DingTalkSendResult


class StreamingProgressBridge:
    def __init__(
        self,
        *,
        responder: DingTalkResponder,
        message: DingTalkInboundMessage,
        interval_ms: int,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        if clock_ms is None:
            self._buffer = StreamUpdateBuffer(interval_ms=interval_ms)
        else:
            self._buffer = StreamUpdateBuffer(
                interval_ms=interval_ms,
                clock_ms=clock_ms,
            )
        self._responder = responder
        self._message = message
        self.send_results: list[DingTalkSendResult] = []

    def emit(self, chunk: str) -> None:
        self._buffer.add(chunk)
        self._flush(force=False)

    def finish(self) -> None:
        self._flush(force=True)

    def _flush(self, *, force: bool) -> None:
        text = self._buffer.flush(force=force)
        if not text:
            return
        result = self._responder.reply_text(self._message, text)
        self.send_results.append(result)
```

- [ ] **Step 4: Run progress bridge tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_streaming_progress_bridge.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/progress.py tests/unit/test_streaming_progress_bridge.py
git commit -m "feat: bridge worker progress to dingtalk replies"
```

Expected: commit succeeds.

## Task 3: Optimization Task Service

**Files:**
- Create: `src/chatdba/tasks/service.py`
- Test: `tests/unit/test_optimization_task_service.py`

- [ ] **Step 1: Write the failing task service tests**

Create `tests/unit/test_optimization_task_service.py`:

```python
from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.service import OptimizationTaskService


def make_context() -> DingTalkContext:
    return DingTalkContext(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        session_webhook="https://example.test/webhook",
    )


def test_task_service_builds_payload_and_runs_worker():
    seen = {}

    def fake_runner(task_payload, collector, progress_sink=None):
        seen["task_payload"] = task_payload
        seen["collector"] = collector
        seen["progress_sink"] = progress_sink
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return {"findings": []}

    progress = []
    collector = object()
    service = OptimizationTaskService(
        collector=collector,
        task_runner=fake_runner,
        task_id_factory=lambda: "task-1",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
        progress_sink=progress.append,
    )

    assert execution.task_id == "task-1"
    assert execution.status == TaskStatus.COMPLETED
    assert execution.result == {"findings": []}
    assert execution.error is None
    assert seen["collector"] is collector
    assert seen["progress_sink"] is progress.append
    assert seen["task_payload"]["task_id"] == "task-1"
    assert seen["task_payload"]["raw_sql"] == "select * from orders"
    assert seen["task_payload"]["dingtalk"]["conversation_id"] == "conv-1"
    assert progress == ["Parsing SQL\n"]


def test_task_service_converts_runner_exception_to_failed_execution():
    def failing_runner(task_payload, collector, progress_sink=None):
        raise RuntimeError("collector unavailable")

    service = OptimizationTaskService(
        collector=object(),
        task_runner=failing_runner,
        task_id_factory=lambda: "task-2",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
    )

    assert execution.task_id == "task-2"
    assert execution.status == TaskStatus.FAILED
    assert execution.result is None
    assert execution.error == "collector unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_optimization_task_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.tasks.service'`.

- [ ] **Step 3: Implement the task service**

Create `src/chatdba/tasks/service.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from chatdba.domain.models import DingTalkContext, SqlOptimizationRequest, TaskStatus
from chatdba.worker.run_task import ProgressSink, run_sql_optimization_task


class OptimizationTaskRunner(Protocol):
    def __call__(
        self,
        task_payload: dict[str, object],
        collector,
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
        task_runner: OptimizationTaskRunner = run_sql_optimization_task,
        task_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._collector = collector
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
```

- [ ] **Step 4: Run task service tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_optimization_task_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/tasks/service.py tests/unit/test_optimization_task_service.py
git commit -m "feat: add optimization task service"
```

Expected: commit succeeds.

## Task 4: DingTalk SQL Optimization Handler

**Files:**
- Create: `src/chatdba/dingtalk/handler.py`
- Test: `tests/unit/test_dingtalk_handler.py`

- [ ] **Step 1: Write the failing handler tests**

Create `tests/unit/test_dingtalk_handler.py`:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX,
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    SQL_OPTIMIZATION_USAGE_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkSendResult
from chatdba.domain.models import TaskStatus
from chatdba.tasks.service import OptimizationTaskExecution


class RecordingResponder:
    def __init__(self):
        self.messages = []

    def reply_text(self, message, text):
        self.messages.append(text)
        return DingTalkSendResult(
            conversation_id=message.conversation_id,
            message=text,
            ok=True,
        )


class SuccessfulTaskService:
    def __init__(self):
        self.calls = []

    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        self.calls.append(
            {
                "raw_sql": raw_sql,
                "dingtalk_context": dingtalk_context,
                "progress_sink": progress_sink,
            }
        )
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return OptimizationTaskExecution(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            result={"findings": []},
        )


class FailedTaskService:
    def run_sql(self, *, raw_sql, dingtalk_context, progress_sink=None):
        return OptimizationTaskExecution(
            task_id="task-2",
            status=TaskStatus.FAILED,
            error="collector unavailable",
        )


def make_message(text: str) -> DingTalkInboundMessage:
    return DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text=text,
        session_webhook="https://example.test/webhook",
    )


def test_handler_sends_usage_guidance_for_empty_sql():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化"))

    assert result.accepted is False
    assert result.status == TaskStatus.FAILED
    assert responder.messages == [SQL_OPTIMIZATION_USAGE_MESSAGE]
    assert service.calls == []


def test_handler_runs_task_and_sends_start_progress_and_success():
    responder = RecordingResponder()
    service = SuccessfulTaskService()
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-1"
    assert result.status == TaskStatus.COMPLETED
    assert service.calls[0]["raw_sql"] == "select * from orders"
    assert service.calls[0]["dingtalk_context"].conversation_id == "conv-1"
    assert responder.messages == [
        SQL_OPTIMIZATION_STARTED_MESSAGE,
        "Parsing SQL\n",
        SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    ]


def test_handler_sends_failure_message_when_task_fails():
    responder = RecordingResponder()
    handler = DingTalkSqlOptimizationHandler(
        task_service=FailedTaskService(),
        responder=responder,
        stream_interval_ms=1000,
    )

    result = handler.handle(make_message("SQL优化 select * from orders"))

    assert result.accepted is True
    assert result.task_id == "task-2"
    assert result.status == TaskStatus.FAILED
    assert result.error == "collector unavailable"
    assert responder.messages[-1] == (
        f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}collector unavailable"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_handler.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.handler'`.

- [ ] **Step 3: Implement the handler**

Create `src/chatdba/dingtalk/handler.py`:

```python
from dataclasses import dataclass, field
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.responder import DingTalkResponder, DingTalkSendResult
from chatdba.domain.models import DingTalkContext, TaskStatus
from chatdba.tasks.service import OptimizationTaskExecution
from chatdba.worker.run_task import ProgressSink


SQL_OPTIMIZATION_USAGE_MESSAGE = (
    "请发送需要优化的 SQL，例如：\n"
    "SQL优化\n"
    "select * from orders where user_id = 100;"
)
SQL_OPTIMIZATION_STARTED_MESSAGE = "已收到 SQL 优化请求，开始分析执行计划和元数据。"
SQL_OPTIMIZATION_SUCCESS_MESSAGE = "SQL 优化分析完成。"
SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX = "SQL 优化任务失败："


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
            send_results.append(self._responder.reply_text(message, SQL_OPTIMIZATION_USAGE_MESSAGE))
            return DingTalkHandleResult(
                accepted=False,
                status=TaskStatus.FAILED,
                error="empty sql",
                send_results=send_results,
            )

        send_results.append(self._responder.reply_text(message, SQL_OPTIMIZATION_STARTED_MESSAGE))
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
            return DingTalkHandleResult(
                accepted=True,
                status=TaskStatus.FAILED,
                error=error,
                send_results=send_results,
            )

        bridge.finish()
        send_results.extend(bridge.send_results)

        if execution.status == TaskStatus.FAILED:
            error = execution.error or "unknown error"
            send_results.append(
                self._responder.reply_text(
                    message,
                    f"{SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX}{error}",
                )
            )
            return DingTalkHandleResult(
                accepted=True,
                task_id=execution.task_id,
                status=TaskStatus.FAILED,
                error=error,
                send_results=send_results,
            )

        send_results.append(self._responder.reply_text(message, SQL_OPTIMIZATION_SUCCESS_MESSAGE))
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
```

- [ ] **Step 4: Run handler tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_handler.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/handler.py tests/unit/test_dingtalk_handler.py
git commit -m "feat: handle dingtalk sql optimization messages"
```

Expected: commit succeeds.

## Task 5: Runtime Return Contract

**Files:**
- Modify: `src/chatdba/dingtalk/stream_runtime.py`
- Test: `tests/unit/test_dingtalk_runtime.py`

- [ ] **Step 1: Write the failing runtime test**

Create `tests/unit/test_dingtalk_runtime.py`:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime


def test_runtime_returns_handler_result_for_test_message():
    message = DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
    )

    runtime = DingTalkStreamRuntime(handler=lambda inbound: {"message_id": inbound.message_id})

    assert runtime.handle_test_message(message) == {"message_id": "msg-1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runtime.py -v
```

Expected: FAIL because `handle_test_message` returns `None`.

- [ ] **Step 3: Implement the runtime return contract**

Modify `src/chatdba/dingtalk/stream_runtime.py`:

```python
from collections.abc import Callable
from typing import Any

from chatdba.dingtalk.channel import DingTalkInboundMessage


MessageHandler = Callable[[DingTalkInboundMessage], Any]


class DingTalkStreamRuntime:
    def __init__(self, handler: MessageHandler) -> None:
        self._handler = handler

    def handle_test_message(self, message: DingTalkInboundMessage) -> Any:
        return self._handler(message)
```

- [ ] **Step 4: Run runtime tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runtime.py tests/unit/test_dingtalk_channel.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/stream_runtime.py tests/unit/test_dingtalk_runtime.py
git commit -m "feat: return dingtalk runtime handler results"
```

Expected: commit succeeds.

## Task 6: DingTalk E2E Integration Contract

**Files:**
- Create: `tests/integration/test_dingtalk_e2e_flow.py`
- Modify: `README.md`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_dingtalk_e2e_flow.py`:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime
from chatdba.domain.models import TaskStatus
from chatdba.tasks.service import OptimizationTaskService


class RecordingSender:
    def __init__(self):
        self.messages = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.messages.append(
            {
                "conversation_id": conversation_id,
                "session_webhook": session_webhook,
                "text": text,
            }
        )


def test_dingtalk_runtime_runs_sql_optimization_and_streams_replies():
    def fake_runner(task_payload, collector, progress_sink=None):
        assert task_payload["raw_sql"] == "select * from orders"
        if progress_sink:
            progress_sink("Parsing SQL\n")
            progress_sink("Generated diagnostic findings\n")
        return {"findings": []}

    sender = RecordingSender()
    service = OptimizationTaskService(
        collector=object(),
        task_runner=fake_runner,
        task_id_factory=lambda: "task-1",
    )
    handler = DingTalkSqlOptimizationHandler(
        task_service=service,
        responder=DingTalkResponder(sender),
        stream_interval_ms=1000,
    )
    runtime = DingTalkStreamRuntime(handler=handler.handle)

    result = runtime.handle_test_message(
        DingTalkInboundMessage(
            message_id="msg-1",
            conversation_id="conv-1",
            sender_id="user-1",
            text="SQL优化 select * from orders",
            session_webhook="https://example.test/webhook",
        )
    )

    assert result.task_id == "task-1"
    assert result.status == TaskStatus.COMPLETED
    assert [message["text"] for message in sender.messages] == [
        SQL_OPTIMIZATION_STARTED_MESSAGE,
        "Parsing SQL\nGenerated diagnostic findings\n",
        SQL_OPTIMIZATION_SUCCESS_MESSAGE,
    ]
```

- [ ] **Step 2: Run integration test**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/integration/test_dingtalk_e2e_flow.py -v
```

Expected: PASS because Tasks 1-5 already implemented the required behavior.

- [ ] **Step 3: Update README with DingTalk E2E local contract**

Append this section to `README.md`:

````markdown
## DingTalk E2E Flow

Phase 1 now includes an in-process DingTalk message handling contract:

```text
DingTalkInboundMessage
  -> DingTalkSqlOptimizationHandler
  -> OptimizationTaskService
  -> run_sql_optimization_task
  -> StreamingProgressBridge
  -> DingTalkResponder
```

For local tests, DingTalk sending is represented by an injectable sender object.
Production DingTalk Stream wiring should provide a sender that calls the DingTalk
session webhook or official SDK.

Accepted message examples:

```text
SQL优化
select * from orders where user_id = 100;
```

```text
优化 select * from orders where user_id = 100;
```

````text
```sql
select * from orders where user_id = 100;
```
````
````

- [ ] **Step 4: Run integration and full test suite**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/integration/test_dingtalk_e2e_flow.py -v
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest -q
```

Expected: both commands PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md tests/integration/test_dingtalk_e2e_flow.py
git commit -m "test: cover dingtalk e2e optimization flow"
```

Expected: commit succeeds.

## Task 7: Final Verification

**Files:**
- No code files

- [ ] **Step 1: Run local check script**

Run:

```bash
PYTHON_BIN=/tmp/chatdba-venv/bin/python ./scripts/run-local-checks.sh
```

Expected: all tests pass.

- [ ] **Step 2: Confirm git status**

Run:

```bash
git status --short --branch
```

Expected: clean feature branch with no uncommitted changes.

- [ ] **Step 3: Summarize commits**

Run:

```bash
git log --oneline main..HEAD
```

Expected: shows the DingTalk E2E design and implementation commits.

## Self-Review Checklist

Spec coverage:

- SQL extraction is covered by existing `extract_sql_from_message` and Task 4.
- Empty SQL guidance is covered by Task 4.
- Worker execution is covered by Task 3 and Task 6.
- Progress buffering and forced flush are covered by Task 2.
- DingTalk reply abstraction is covered by Task 1.
- Runtime handler return contract is covered by Task 5.
- End-to-end local contract is covered by Task 6.

Type consistency:

- `DingTalkInboundMessage` is reused from `chatdba.dingtalk.channel`.
- `DingTalkContext`, `SqlOptimizationRequest`, and `TaskStatus` are reused from `chatdba.domain.models`.
- `ProgressSink` is reused from `chatdba.worker.run_task`.
- `DingTalkSendResult` is shared by responder, progress bridge, and handler.
- `OptimizationTaskExecution` is shared by task service and handler.

Execution order:

- Task 1 must run before Task 2 because the progress bridge depends on `DingTalkResponder`.
- Task 2 and Task 3 must run before Task 4 because the handler depends on both.
- Task 5 can run after Task 4 or in parallel with Task 3, but the plan keeps it sequential for simpler review.
- Task 6 validates the full chain after all units exist.
