# ChatDBA

ChatDBA phase 1 provides DingTalk-based SQL optimization using a controlled LangGraph workflow and Tongyi Qianwen generation.

## Local Checks

```bash
pip install -e ".[dev]"
pytest -q
```

## Phase 1 Local Runbook

1. Start dependencies:

```bash
docker compose up -d
```

2. Install Python dependencies:

```bash
pip install -e ".[dev]"
```

3. Run tests:

```bash
./scripts/run-local-checks.sh
```

If your dependencies are installed in a specific virtual environment, point the
script at that interpreter:

```bash
PYTHON_BIN=/path/to/venv/bin/python ./scripts/run-local-checks.sh
```

4. Start API:

```bash
uvicorn chatdba.app.main:app --reload
```

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

## Real DingTalk Runtime

ChatDBA now includes a real DingTalk transport layer for Stream mode. The runtime
receives chatbot callback payloads from DingTalk, maps them into
`DingTalkInboundMessage`, and replies through the inbound `sessionWebhook`.

Start the runtime with:

```bash
chatdba-dingtalk
```

Equivalent module form:

```bash
python -m chatdba.dingtalk.runner
```

Required settings:

```text
DINGTALK_STREAM_ENABLED=true
DINGTALK_CLIENT_ID=replace-with-client-id
DINGTALK_CLIENT_SECRET=replace-with-client-secret
```

Current runtime boundary:

- DingTalk transport is real.
- SQL optimization still uses the current in-process collector wiring.
- Until a production MySQL collector is configured, SQL requests received from
  DingTalk will fail with a clear collector configuration message instead of
  silently pretending to optimize against a real database.
