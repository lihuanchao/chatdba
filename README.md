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

5. Optional: backfill case embeddings for pgvector retrieval:

```bash
python scripts/backfill_case_embeddings.py --limit 100
```

6. Optional: import sample optimization cases for retrieval validation:

```bash
psql "$DATABASE_URL" -f examples/seed_optimization_cases.sql
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
DINGTALK_AI_CARD_TEMPLATE_ID=optional-default-template-id
DINGTALK_AI_CARD_CONTENT_FIELD=content
CASE_RETRIEVAL_VECTOR_TOP_K=12
CASE_RETRIEVAL_CANDIDATE_LIMIT=12
```

Card template selection supports two modes:

- default template via `DINGTALK_AI_CARD_TEMPLATE_ID`
- per-message override by adding a control line in chat text:

```text
模板ID: your-template-id
SQL优化
select * from orders where user_id = 100;
```

or

```text
template_id=your-template-id
SQL优化 select * from orders where user_id = 100;
```

Compatibility note:

- For custom template IDs, ChatDBA follows the same pattern as `dify-on-dingtalk`: create card with `callbackType=STREAM`, then stream updates through `/v1.0/card/streaming`.
- `DINGTALK_AI_CARD_CONTENT_FIELD` controls the streaming key and template parameter name (default `content`).
- If card creation or streaming fails, ChatDBA automatically degrades to plain text reply in the same conversation.

## Metadata Routing And Degraded Analysis

ChatDBA can now route SQL through the metadata database to a source MySQL instance
and try to collect real `EXPLAIN FORMAT=JSON` plus `SHOW CREATE TABLE`.

Current routing behavior:

- single-instance SQL is routed to the source database,
- cross-instance SQL degrades to SQL-only analysis,
- missing metadata route degrades to SQL-only analysis,
- failed source evidence collection still returns an optimization report.

Required metadata settings:

```text
METADATA_MYSQL_HOST=
METADATA_MYSQL_PORT=3306
METADATA_MYSQL_USER=
METADATA_MYSQL_PASSWORD=
METADATA_MYSQL_DATABASE=
METADATA_ROUTE_TABLE=table_routes
METADATA_INSTANCE_TABLE=db_instances
```

Evidence levels in the final report:

- `full`
- `partial`
- `sql_only`

## Hybrid Case Retrieval

ChatDBA now supports optional hybrid case retrieval for SQL optimization history:

- structured rule filtering remains the primary stable path,
- when `optimization_cases.embedding` has data and Qwen embedding is configured,
- the runtime adds pgvector TopK recall and merges those hits with rule candidates,
- if embedding generation or pgvector query fails, retrieval automatically falls back to rule-only mode.

To backfill embeddings for existing historical cases:

```bash
python scripts/backfill_case_embeddings.py --limit 100
```
