# Metadata Routing And Degraded SQL Optimization Design

## Goal

Extend ChatDBA from transport-complete to evidence-aware optimization: route a user SQL statement through the metadata database to the correct source MySQL instance, collect real execution evidence from the source database when possible, and still generate an optimization report when routing or evidence collection fails.

The defining product behavior is:

- users still only submit SQL,
- the system tries to find the correct source database automatically,
- the system prefers real evidence from the source database,
- the system never returns "nothing" just because evidence is missing.

## Scope

In scope:

- Route SQL table references through the metadata database to source MySQL instances.
- Support only SQL that resolves to a single source instance.
- Connect to the source MySQL instance with a read-only connection and collect:
  - `EXPLAIN FORMAT=JSON`
  - `SHOW CREATE TABLE`
- Continue analysis when route lookup fails, when the SQL spans multiple instances, or when source evidence collection fails.
- Classify evidence completeness as `full`, `partial`, or `sql_only`.
- Surface evidence gaps and lower confidence in the final optimization report.
- Wire the real routed collector into the existing DingTalk runtime.

Out of scope:

- Cross-instance explain or federated plan analysis.
- Automatic multi-instance SQL splitting.
- Metadata synchronization jobs.
- Connection pooling or large-scale concurrency tuning.
- Approval workflow or DBA sign-off features.

## Product Behavior

The system now follows a best-effort evidence strategy rather than an all-or-nothing strategy.

1. Parse SQL and identify table references.
2. Query the metadata database to find the owning source instance for each table.
3. If every table resolves to the same instance, try to collect real source evidence.
4. If routing fails, resolves to multiple instances, or evidence collection fails, continue anyway.
5. Generate a final optimization report using the best available evidence level.

That means the system supports three evidence levels:

- `full`: SQL, route information, execution plan, and table DDL are available.
- `partial`: SQL is available and only some evidence is available.
- `sql_only`: only the SQL text is available, with no reliable route/evidence payload.

## Architecture

The new path extends the existing workflow with routing and degraded evidence handling.

```text
DingTalk SQL request
  -> SQL parser
  -> MetadataRouter
  -> RoutedMysqlEvidenceCollector
      -> SourceMysqlConnectionFactory
      -> RuntimeMysqlClient
      -> MysqlEvidenceCollector
  -> EvidenceEnvelope
  -> Rule analysis + case retrieval + LLM report generation
  -> Optimization report with evidence_status/confidence/limitations
```

The routing layer decides where to try evidence collection. The evidence collector layer attempts real MySQL collection. The report layer no longer assumes evidence exists; it consumes an evidence envelope that can carry success, failure, or partial success.

## Components

### `MetadataRouter`

`MetadataRouter` resolves parsed table references against the metadata database.

Responsibilities:

- Query the metadata database for each referenced table.
- Return a single-instance route when all tables resolve to the same source instance.
- Detect these failure cases:
  - no route found for one or more tables,
  - one or more routes are disabled,
  - multiple different source instances are involved.
- Return structured route diagnostics so the report can explain why evidence is missing.

The router is authoritative for target selection, but not for table DDL or execution plans. Those come from the source database.

### `SourceMysqlConnectionFactory`

`SourceMysqlConnectionFactory` builds a read-only MySQL client for the routed source instance.

Responsibilities:

- Accept route information from `MetadataRouter`.
- Use configured timeouts.
- Build the low-level MySQL client used by `MysqlEvidenceCollector`.
- Keep source-database connection creation isolated from workflow and report code.

This component is the seam where a simple direct connection can later be replaced with pooling or a secret manager without changing workflow code.

### `RuntimeMysqlClient`

`RuntimeMysqlClient` is a concrete `MysqlClient` implementation for real MySQL access.

Responsibilities:

- Execute `query_one(sql)` against a live source MySQL connection.
- Return row dictionaries in the shape already expected by `MysqlEvidenceCollector`.
- Raise structured runtime errors for:
  - connection failure,
  - authentication failure,
  - timeout,
  - SQL execution failure.

This lets the existing `MysqlEvidenceCollector` stay small and focused on evidence extraction rather than connection management.

### `RoutedMysqlEvidenceCollector`

`RoutedMysqlEvidenceCollector` is the high-level collector that the runtime will use.

Responsibilities:

- Accept raw SQL and parsed table targets.
- Call `MetadataRouter`.
- If routing succeeds to one instance, build a source client and call `MysqlEvidenceCollector.collect(...)`.
- If routing fails or collection fails, return a structured degraded evidence result rather than raising a hard-stop error to the report layer.

This component turns source routing and evidence collection into a single interface that the workflow can depend on.

### `EvidenceEnvelope`

The workflow should stop treating evidence as "always present". Instead it should consume an envelope object with:

- `status`: `full`, `partial`, `sql_only`
- `route`: optional route metadata
- `explain_json`: optional
- `create_tables`: optional
- `missing_evidence`: list of missing evidence keys
- `collection_errors`: list of safe user-facing error summaries

This envelope is the key to degraded analysis. It allows the report layer to continue while still being explicit about what is missing.

## Metadata Database Shape

The metadata database already contains consolidated schema information, so this design does not require a brand-new schema. It only requires the application to be able to query the equivalent of these two conceptual datasets:

### Source instance dataset

At minimum, the runtime must be able to retrieve:

- `instance_id`
- `db_type`
- `host`
- `port`
- `readonly_username`
- `readonly_password_ref` or equivalent secret lookup key
- `default_schema`
- `version`
- `env`
- `enabled`

### Table route dataset

At minimum, the runtime must be able to retrieve:

- `schema_name`
- `table_name`
- `instance_id`
- `is_active`
- `last_synced_at`

Whether these live as tables, views, or an existing metadata model is an implementation choice. The code only needs a repository interface that returns the fields above.

## Routing Rules

Phase 1 routing behavior is intentionally strict:

- Single-table SQL: route directly by that table.
- Multi-table SQL: continue only if every table resolves to the same source instance.
- Table missing from metadata: route failure.
- Mixed source instances: route failure.

A route failure does not stop report generation. It only forces the workflow down to `sql_only`.

## Degraded Analysis Rules

This requirement is central to the design.

### Full Evidence

Use when:

- route success,
- execution plan success,
- table DDL success.

Report behavior:

- confidence is high,
- recommendations may reference real plan symptoms,
- validation steps focus on confirming proposed rewrites/indexes on the target database.

### Partial Evidence

Use when:

- route success but only some evidence is available,
- for example explain succeeded but DDL failed,
- or DDL succeeded but explain failed.

Report behavior:

- confidence is medium,
- recommendations can still use partial evidence,
- report explicitly lists missing evidence and warns that some conclusions are inferred.

### SQL Only

Use when:

- route lookup failed,
- SQL spans multiple instances,
- source connection could not be established,
- both explain and DDL collection failed.

Report behavior:

- confidence is low,
- report relies on SQL structure, rules, and similar cases,
- report explicitly says it is not based on source execution evidence.

## Report Changes

The final optimization report should be extended with fields that explain evidence quality.

Add these concepts to the report model:

- `evidence_status`: `full`, `partial`, `sql_only`
- `missing_evidence`: list of evidence keys such as `route_info`, `explain_json`, `create_table`
- `limitations`: list of human-readable caveats
- `confidence_label`: `high`, `medium`, `low`

The existing numeric `confidence` field can stay, but the explicit label is easier to present in DingTalk.

## User-Facing Messaging

The report and DingTalk reply flow should clearly communicate when evidence is missing.

Examples:

- `已获取执行计划和表结构，以下为基于真实证据的优化建议。`
- `未获取到完整执行计划/表结构，以下建议基于部分证据、SQL 规则和历史案例生成，请在目标库验证。`
- `当前无法获取源库执行计划，以下为 SQL 级别优化建议，置信度较低。`
- `当前 SQL 涉及多个数据库实例，暂不支持自动采集真实执行计划，以下为 SQL 级别优化建议。`

The user should never be left guessing whether a recommendation came from real evidence or inference.

## Error Handling

Errors should be captured as structured diagnostics rather than hard-stopping the workflow.

### Routing errors

- table missing from metadata,
- source instance disabled,
- multiple source instances involved.

### Source connection errors

- connection timeout,
- authentication failure,
- host unavailable.

### Evidence collection errors

- `EXPLAIN FORMAT=JSON` failure,
- `SHOW CREATE TABLE` failure,
- malformed payload parsing.

### Input/safety errors

- empty SQL,
- unsupported SQL type,
- parser failure.

Routing and evidence errors downgrade the analysis. Safety and invalid-input errors still stop execution when the SQL should not be analyzed at all.

## Testing Strategy

Testing should be layered.

### Routing tests

- single-table route success,
- multi-table single-instance route success,
- multi-instance route failure,
- missing-table route failure.

### Source evidence tests

- explain success,
- DDL success,
- explain-only success,
- DDL-only success,
- both fail.

### Degraded workflow tests

- full evidence produces `full` status,
- partial evidence produces `partial` status,
- route failure produces `sql_only`,
- cross-instance route failure still produces a report.

### DingTalk integration tests

- DingTalk request returns a report even when route lookup fails,
- DingTalk request returns a report even when source evidence collection fails,
- final message clearly reflects evidence completeness.

## Implementation Order

To keep the change controlled, implement in this order:

1. metadata route repository and router,
2. source MySQL runtime client and connection factory,
3. routed collector with degraded evidence envelope,
4. workflow/report changes for `full/partial/sql_only`,
5. DingTalk runtime wiring and user-facing evidence messaging.

This keeps transport changes small while the data-plane logic evolves.

## Future Follow-Up

Once the single-instance route path is stable, later work can add:

- multi-instance detection UX improvements,
- secret manager integration,
- connection pooling,
- richer case retrieval conditioned on evidence level,
- broader database support beyond MySQL.
