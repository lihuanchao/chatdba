# ChatDBA SQL Optimization Phase 1 Design

## Goal

Build the first phase of ChatDBA around SQL optimization. Users submit SQL from a DingTalk chat window, ChatDBA collects database evidence, runs a controlled optimization workflow, streams progress and analysis back to DingTalk, and finally returns a structured optimization report.

Phase 1 focuses on MySQL-style SQL optimization using `EXPLAIN FORMAT=JSON`, table metadata, deterministic rules, historical cases, and Tongyi Qianwen generation. The system should be evidence-driven rather than a free-form autonomous agent.

## Non-Goals

- Do not execute user SQL directly.
- Do not allow the model to freely run database tools.
- Do not create, drop, or modify indexes automatically.
- Do not use Dify as the orchestration platform.
- Do not introduce OpenSearch, Temporal, or multi-agent collaboration in phase 1 unless the first version proves the need.

## Recommended Architecture

```text
DingTalk chat
  -> DingTalk enterprise app robot / AI assistant
  -> DingTalk Channel Service
  -> Redis Streams / task queue
  -> LangGraph SQL Optimization Workflow
  -> PostgreSQL + pgvector
  -> Tongyi Qianwen Model Gateway
  -> DingTalk streaming response
```

The architecture is workflow-first. LangGraph owns orchestration and state transitions. Ordinary engineering modules own metadata, database access, retrieval, rules, validation, and DingTalk messaging.

## Components

### DingTalk Channel Service

Responsible for all DingTalk-facing behavior:

- Receive user messages through DingTalk Stream Mode.
- Deduplicate messages by DingTalk message id and internal task id.
- Extract SQL from single-chat or group-chat messages.
- Map DingTalk users to ChatDBA users and database permissions.
- Send immediate acknowledgements so DingTalk does not retry.
- Create and update a streaming response message.
- Throttle streaming updates to avoid excessive DingTalk API calls.
- Render final reports as Markdown or DingTalk interactive cards.

Preferred implementation:

- Use DingTalk enterprise internal app robot or DingTalk AI assistant.
- Use Stream Mode for receiving messages.
- Use AI assistant stepwise message update when available.
- Fall back to robot Markdown/card update when AI assistant streaming update is not available.

### API And Task Layer

Responsible for internal service boundaries:

- `POST /internal/tasks/sql-optimization` creates an optimization task.
- `GET /internal/tasks/{task_id}` returns task status and final result.
- `POST /dingtalk/events` is optional if webhook mode is needed later.
- Redis Streams or a lightweight queue carries progress events and model chunks.

Task states:

- `received`
- `parsing_sql`
- `resolving_database`
- `collecting_metadata`
- `collecting_explain`
- `diagnosing`
- `retrieving_cases`
- `generating_report`
- `validating`
- `completed`
- `failed`

### LangGraph Workflow

LangGraph is used as the workflow orchestration layer, not as the whole system.

Recommended nodes:

- `normalize_sql`: normalize SQL, remove unsafe content, generate SQL fingerprint.
- `parse_sql`: extract tables, aliases, selected columns, predicates, joins, order/group/limit.
- `resolve_database`: find target database instance and schema from the global metadata repository.
- `collect_metadata`: collect `SHOW CREATE TABLE`, indexes, row counts, partitions, and statistics.
- `collect_explain`: collect `EXPLAIN FORMAT=JSON` through a read-only database connector.
- `parse_explain`: convert explain JSON into internal plan features.
- `rule_diagnosis`: run deterministic SQL optimization rules.
- `retrieve_cases`: retrieve similar historical optimization cases from PostgreSQL + pgvector.
- `generate_recommendations`: call Tongyi Qianwen with structured evidence.
- `validate_recommendations`: validate SQL syntax, index risk, version compatibility, and conflicts.
- `generate_report`: produce the final report and case feedback payload.

The workflow emits progress events after each node. The DingTalk Channel Service consumes these events and updates the user-facing message.

### Database Connector

Responsible for database-safe evidence collection:

- Use read-only credentials.
- Enforce SQL type allowlist.
- Run `EXPLAIN FORMAT=JSON` only.
- Run metadata queries such as `SHOW CREATE TABLE`, `SHOW INDEX`, and `information_schema` queries.
- Apply per-query timeout, retry, and circuit breaking.
- Never expose arbitrary SQL execution to the model.

`EXPLAIN ANALYZE` is out of scope for phase 1 because it may execute the SQL and can be risky in production.

### Metadata And Case Store

Use PostgreSQL as the control plane:

- Store tasks, user mappings, database instance mappings, metadata snapshots, reports, and feedback.
- Store historical optimization cases as structured objects.
- Use pgvector for case embedding retrieval.

Recommended case fields:

- `db_type`
- `db_version`
- `sql_fingerprint`
- `scenario_tags`
- `plan_features`
- `root_cause_tags`
- `table_features`
- `index_features`
- `optimization_actions`
- `before_after_metrics`
- `case_card`
- `full_text`
- `embedding`
- `quality_score`

Phase 1 retrieval should use structured filters first, then vector similarity. OpenSearch can be added later when case volume and recall requirements grow.

### Rule Engine

Rules provide deterministic guardrails before LLM generation:

- Full table scan on large table.
- Missing join index.
- Function-wrapped indexed columns.
- Implicit type conversion.
- Leading wildcard `LIKE`.
- Filesort or temporary table caused by order/group pattern.
- Deep pagination.
- Low-selectivity or redundant index recommendation risk.
- MySQL version-specific limitations.

Rule outputs should be structured and passed to Tongyi Qianwen as evidence, not as prose-only hints.

### Tongyi Qianwen Model Gateway

The model gateway isolates application code from model provider details.

Recommended model roles:

- `qwen-plus`: default SQL diagnosis, recommendation generation, and report generation.
- `qwen-max`: complex SQL, low-confidence retry, or difficult execution plans.
- `qwen-flash`: cheap summarization, case card generation, and background labeling.
- `text-embedding-v4`: historical case embeddings.

Generation constraints:

- Use streaming output for final report generation.
- Ask the model to output strict JSON for machine-checked recommendations.
- Validate model output with Pydantic or JSON Schema.
- If validation fails, route to a LangGraph repair node.
- Keep database evidence structured and compact.

The model should not decide which database SQL to execute. It receives collected evidence and produces diagnosis, candidate rewrites, index recommendations, risks, and validation steps.

## Streaming Design

Streaming has two layers:

- Workflow progress streaming: each workflow node emits human-readable progress.
- Model token streaming: Tongyi Qianwen emits report chunks during final generation.

Recommended update policy:

- Send an immediate DingTalk acknowledgement after receiving the message.
- Create or prepare a single response message.
- Update that message every 800-1500 ms or at section boundaries.
- Buffer small model chunks before sending to DingTalk.
- Always finish by sending a final stable report with task id.

Example user-facing progress:

```text
已收到 SQL，正在解析...
已定位到数据库实例和 schema...
正在获取表结构、索引和统计信息...
正在获取 EXPLAIN FORMAT=JSON...
发现疑似问题：全表扫描、filesort...
正在检索相似优化案例...
正在生成优化建议...
```

## Final Report Shape

The final report should be rendered for DingTalk while preserving a structured source payload.

Required sections:

- Summary: key bottlenecks and confidence.
- Evidence: relevant plan features and metadata evidence.
- SQL rewrite: original and suggested SQL.
- Index recommendations: DDL, expected benefit, and risk.
- Explanation: why the recommendation should help.
- Validation: how to verify safely.
- Rollback: how to undo risky changes.
- Similar cases: top matched cases and matching reasons.

## Safety Requirements

- Only authorized DingTalk users can request optimization for mapped database domains.
- Database access must use read-only accounts.
- SQL validation must reject DML, DDL, multiple statements, comments with suspicious payloads, and unsupported database types.
- Source SQL and metadata should be masked according to company policy before model calls if needed.
- All model prompts, evidence snapshots, recommendations, and final reports must be auditable by `task_id`.
- Any index recommendation must be advisory only and require DBA approval before execution.

## Phase 1 Scope

Build these first:

- DingTalk Stream message receiving.
- DingTalk streaming response abstraction.
- SQL optimization task service.
- LangGraph workflow with core nodes.
- MySQL metadata and explain collector.
- PostgreSQL task, metadata, case, and feedback tables.
- pgvector case retrieval.
- Rule engine with initial MySQL rules.
- Tongyi Qianwen streaming model gateway.
- Structured JSON report validation.

Defer these:

- OpenSearch hybrid retrieval.
- Temporal durable execution.
- Multi-agent collaboration.
- Automatic index execution.
- `EXPLAIN ANALYZE` on production databases.
- Multi-database support beyond the first supported database type.

## Success Criteria

- A DingTalk user can submit SQL and receive streamed progress in the chat window.
- The system can identify the target database and collect required evidence without executing the SQL.
- The report includes concrete evidence, SQL rewrite suggestions, index suggestions, validation steps, and risks.
- Model output is schema-validated before being shown as the final recommendation.
- Each task is traceable from DingTalk message id to evidence, model input, model output, and final report.
