# DingTalk End-To-End SQL Optimization Design

## Goal

Build the first usable DingTalk-to-ChatDBA loop: a user sends SQL in a DingTalk chat, ChatDBA extracts the SQL, runs the existing SQL optimization workflow, streams progress updates, and sends a final message back to the same DingTalk conversation.

This is a minimum end-to-end product slice. It prioritizes a working interaction path over production-scale queueing and advanced optimizer quality.

## Scope

In scope:

- Extract SQL from `DingTalkInboundMessage`.
- Reject empty or unrecognized SQL with a helpful DingTalk reply.
- Execute the existing `run_sql_optimization_task` worker entrypoint.
- Bridge worker progress chunks through `StreamUpdateBuffer`.
- Send progress and final messages through a responder abstraction.
- Keep DingTalk sending testable without real network calls.
- Keep the existing FastAPI task creation contract intact.

Out of scope:

- Real multi-database routing from the metadata catalog.
- Production Redis queue workers.
- Full DingTalk SDK long-running connection management.
- DBA approval workflow.
- Case rerank training.
- Permission and audit policy beyond the existing SQL safety primitives.

## Architecture

The feature adds a small DingTalk application layer around the workflow that already exists.

```text
DingTalkInboundMessage
  -> DingTalkSqlOptimizationHandler
  -> OptimizationTaskService
  -> run_sql_optimization_task(progress_sink=...)
  -> StreamingProgressBridge
  -> DingTalkResponder
```

The handler owns chat-level behavior: parse the message, decide whether to start a task, and send user-facing status. The task service owns execution behavior: build the task payload, call the worker, and return a structured task result. The streaming bridge owns buffering behavior: collect small progress chunks and flush them to the responder at the configured interval, with a final forced flush at completion.

## Components

### `DingTalkResponder`

`DingTalkResponder` sends text back to DingTalk. It depends on a small `DingTalkTextSender` protocol so tests can use an in-memory fake sender while production can later use session webhook or the official DingTalk SDK.

The responder returns a `DingTalkSendResult` with:

- `ok`: whether the send was accepted by the sender.
- `message`: the text attempted.
- `error`: optional error string if sending failed.

Responder failures do not crash task execution. They are captured so the handler can include them in its result.

### `OptimizationTaskService`

`OptimizationTaskService` receives SQL plus DingTalk context and calls `run_sql_optimization_task`. It accepts:

- `collector`: the existing MySQL evidence collector dependency.
- `task_runner`: defaulting to `run_sql_optimization_task`, injectable for tests.

It returns `OptimizationTaskExecution`:

- `task_id`
- `status`
- `result`
- `error`

For this slice, execution is synchronous in-process. A later Redis worker can preserve the same service interface and move execution out of process.

### `StreamingProgressBridge`

`StreamingProgressBridge` wraps `StreamUpdateBuffer`. Its `emit(chunk)` method adds progress text and flushes if the buffer interval has elapsed. Its `finish()` method force flushes remaining text.

This keeps the worker decoupled from DingTalk and avoids sending one DingTalk message per tiny token or progress fragment.

### `DingTalkSqlOptimizationHandler`

`DingTalkSqlOptimizationHandler` is the orchestration object called by `DingTalkStreamRuntime`.

Behavior:

1. Extract SQL from the inbound DingTalk message.
2. If SQL is empty, reply with usage guidance and stop.
3. Send a start message.
4. Run `OptimizationTaskService` with a progress sink connected to `StreamingProgressBridge`.
5. Force flush remaining progress chunks.
6. Send a final success or failure message.
7. Return a structured `DingTalkHandleResult` for logging and tests.

## Message Format

Accepted user formats:

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

Empty SQL guidance:

```text
请发送需要优化的 SQL，例如：
SQL优化
select * from orders where user_id = 100;
```

Start message:

```text
已收到 SQL 优化请求，开始分析执行计划和元数据。
```

Failure message:

```text
SQL 优化任务失败：<short reason>
```

Success message:

```text
SQL 优化分析完成。
```

## Error Handling

- Empty SQL: no task is created; usage guidance is sent.
- Task runner exception: handler sends a failure message with a short reason.
- Responder exception: the failure is captured in `send_results`; task execution continues.
- Progress bridge send failure: captured and exposed in handler result.

The handler should not expose stack traces or secrets in DingTalk messages.

## Testing Strategy

Tests cover behavior without external network calls:

- Handler sends usage guidance for empty SQL.
- Handler executes task service and sends start/progress/final messages for valid SQL.
- Progress bridge flushes interval-based and forced chunks through the responder.
- Handler converts task exceptions into a DingTalk failure message.
- Existing FastAPI, workflow, parser, Qwen gateway, and report schema tests continue to pass.

## Deployment Notes

This feature still runs in process. A practical local run remains:

```bash
docker compose up -d
pip install -e ".[dev]"
uvicorn chatdba.app.main:app --host 0.0.0.0 --port 8000
```

Production deployment should split API, DingTalk Stream runtime, and worker processes later. This slice keeps the interfaces narrow enough to do that without rewriting handler tests.
