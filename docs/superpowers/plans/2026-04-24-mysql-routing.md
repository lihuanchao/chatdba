# Metadata Routing And Degraded SQL Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route SQL statements through the metadata database to the correct source MySQL instance, collect real source evidence when possible, degrade gracefully to SQL-only analysis when routing or collection fails, and return a final optimization report to DingTalk.

**Architecture:** Introduce a metadata router plus source-MySQL runtime client, wrap their outputs in an `EvidenceEnvelope`, and update the workflow so report generation no longer assumes execution evidence always exists. The DingTalk runtime will use a routed collector and render the final optimization report instead of sending only a generic completion message.

**Tech Stack:** Python 3.11+, Pydantic v2, PyMySQL, LangGraph, existing SQL parser/rules/case retrieval/Qwen gateway, pytest.

---

## File Structure

Create or modify these files:

```text
src/chatdba/domain/models.py
src/chatdba/domain/report_schema.py
src/chatdba/db/mysql_collector.py
src/chatdba/db/metadata_router.py
src/chatdba/db/runtime_mysql.py
src/chatdba/db/routed_collector.py
src/chatdba/workflow/state.py
src/chatdba/workflow/report_builder.py
src/chatdba/workflow/sql_optimization.py
src/chatdba/models/qwen_gateway.py
src/chatdba/worker/run_task.py
src/chatdba/tasks/service.py
src/chatdba/dingtalk/rendering.py
src/chatdba/dingtalk/handler.py
src/chatdba/dingtalk/runtime.py
src/chatdba/config/settings.py
.env.example
README.md
tests/unit/test_report_schema.py
tests/unit/test_mysql_collector.py
tests/unit/test_metadata_router.py
tests/unit/test_runtime_mysql.py
tests/unit/test_routed_collector.py
tests/unit/test_report_builder.py
tests/unit/test_qwen_gateway.py
tests/unit/test_worker_run_task.py
tests/unit/test_optimization_task_service.py
tests/unit/test_dingtalk_handler.py
tests/unit/test_dingtalk_runtime_builder.py
tests/integration/test_workflow_happy_path.py
tests/integration/test_dingtalk_e2e_flow.py
```

Responsibilities:

- `domain.models`: evidence status, route info, evidence envelope.
- `domain.report_schema`: final report fields for evidence completeness.
- `db.mysql_collector`: granular explain/DDLs collection helpers.
- `db.metadata_router`: metadata-db route lookup and single-instance validation.
- `db.runtime_mysql`: live PyMySQL client and source connection factory.
- `db.routed_collector`: high-level routed collector returning an evidence envelope.
- `workflow.report_builder`: deterministic/Qwen-assisted report composition.
- `workflow.sql_optimization`: route/collect/diagnose/report graph.
- `dingtalk.rendering`: convert final report into DingTalk-friendly text.
- `dingtalk.runtime`: build real routed collector from settings and wire runtime.

## Task 1: Evidence Envelope And Report Schema

**Files:**
- Modify: `src/chatdba/domain/models.py`
- Modify: `src/chatdba/domain/report_schema.py`
- Test: `tests/unit/test_report_schema.py`

- [ ] **Step 1: Write the failing schema tests**

Update `tests/unit/test_report_schema.py` to:

```python
import pytest
from pydantic import ValidationError

from chatdba.domain.models import ConfidenceLabel, EvidenceStatus
from chatdba.domain.report_schema import OptimizationReport


def test_report_accepts_required_sections():
    report = OptimizationReport.model_validate(
        {
            "task_id": "task-1",
            "summary": "Full table scan on orders.",
            "confidence": 0.82,
            "confidence_label": "high",
            "evidence_status": "full",
            "missing_evidence": [],
            "limitations": [],
            "bottlenecks": [{"code": "full_table_scan", "evidence": "rows_examined is high"}],
            "sql_rewrites": [{"title": "Use sargable predicate", "sql": "select * from orders where created_at >= ?"}],
            "index_recommendations": [{"ddl": "create index idx_orders_created_at on orders(created_at)", "risk": "medium"}],
            "risks": [{"level": "medium", "description": "Index build needs online DDL review"}],
            "validation_steps": ["Run EXPLAIN FORMAT=JSON on the rewritten SQL"],
            "similar_cases": [{"case_id": "case-1", "reason": "same filesort symptom"}],
        }
    )

    assert report.task_id == "task-1"
    assert report.confidence == 0.82
    assert report.evidence_status == EvidenceStatus.FULL
    assert report.confidence_label == ConfidenceLabel.HIGH


def test_report_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        OptimizationReport.model_validate(
            {
                "task_id": "task-1",
                "summary": "Invalid confidence.",
                "confidence": 1.5,
                "confidence_label": "low",
                "evidence_status": "sql_only",
                "missing_evidence": ["explain_json"],
                "limitations": ["No source execution evidence was available."],
                "bottlenecks": [],
                "sql_rewrites": [],
                "index_recommendations": [],
                "risks": [],
                "validation_steps": [],
                "similar_cases": [],
            }
        )


def test_report_requires_known_evidence_status():
    with pytest.raises(ValidationError):
        OptimizationReport.model_validate(
            {
                "task_id": "task-1",
                "summary": "Invalid status.",
                "confidence": 0.4,
                "confidence_label": "low",
                "evidence_status": "unknown",
                "missing_evidence": [],
                "limitations": [],
                "bottlenecks": [],
                "sql_rewrites": [],
                "index_recommendations": [],
                "risks": [],
                "validation_steps": [],
                "similar_cases": [],
            }
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_report_schema.py -v
```

Expected: FAIL because the new fields and enums do not exist yet.

- [ ] **Step 3: Implement evidence status models and report schema**

Update `src/chatdba/domain/models.py` to:

```python
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    RECEIVED = "received"
    PARSING_SQL = "parsing_sql"
    RESOLVING_DATABASE = "resolving_database"
    COLLECTING_METADATA = "collecting_metadata"
    COLLECTING_EXPLAIN = "collecting_explain"
    DIAGNOSING = "diagnosing"
    RETRIEVING_CASES = "retrieving_cases"
    GENERATING_REPORT = "generating_report"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class EvidenceStatus(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    SQL_ONLY = "sql_only"


class ConfidenceLabel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DingTalkContext(BaseModel):
    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: str | None = None
    session_webhook: str | None = None


class SqlOptimizationRequest(BaseModel):
    task_id: str
    raw_sql: str
    dingtalk: DingTalkContext | None = None


class TableReference(BaseModel):
    schema_name: str | None = None
    table_name: str
    alias: str | None = None


class SqlFeatures(BaseModel):
    fingerprint: str
    statement_type: str
    tables: list[TableReference] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    has_limit: bool = False


class PlanFeature(BaseModel):
    code: str
    severity: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RuleFinding(BaseModel):
    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class SourceRoute(BaseModel):
    instance_id: str
    db_type: str
    version: str | None = None
    host: str | None = None
    port: int | None = None
    default_schema: str | None = None
    credentials: dict[str, str] = Field(default_factory=dict)
    schema_names: list[str] = Field(default_factory=list)


class EvidenceEnvelope(BaseModel):
    status: EvidenceStatus
    route: SourceRoute | None = None
    explain_json: dict[str, object] | None = None
    create_tables: dict[str, str] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    collection_errors: list[str] = Field(default_factory=list)
```

Update `src/chatdba/domain/report_schema.py` to:

```python
from pydantic import BaseModel, Field

from chatdba.domain.models import ConfidenceLabel, EvidenceStatus


class Bottleneck(BaseModel):
    code: str
    evidence: str


class SqlRewrite(BaseModel):
    title: str
    sql: str


class IndexRecommendation(BaseModel):
    ddl: str
    risk: str


class Risk(BaseModel):
    level: str
    description: str


class SimilarCase(BaseModel):
    case_id: str
    reason: str


class OptimizationReport(BaseModel):
    task_id: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: ConfidenceLabel
    evidence_status: EvidenceStatus
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    bottlenecks: list[Bottleneck]
    sql_rewrites: list[SqlRewrite]
    index_recommendations: list[IndexRecommendation]
    risks: list[Risk]
    validation_steps: list[str]
    similar_cases: list[SimilarCase]
```

- [ ] **Step 4: Run schema tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_report_schema.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/domain/models.py src/chatdba/domain/report_schema.py tests/unit/test_report_schema.py
git commit -m "feat: add evidence-aware report schema"
```

Expected: commit succeeds.

## Task 2: Granular MySQL Evidence Collection

**Files:**
- Modify: `src/chatdba/db/mysql_collector.py`
- Test: `tests/unit/test_mysql_collector.py`

- [ ] **Step 1: Write the failing collector tests**

Update `tests/unit/test_mysql_collector.py` to:

```python
from chatdba.db.mysql_collector import MysqlEvidenceCollector, MysqlTableTarget


class FakeMysqlClient:
    def __init__(self):
        self.queries = []

    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {"EXPLAIN": "{\"query_block\":{\"table\":{\"table_name\":\"orders\",\"access_type\":\"ALL\"}}}"}
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


class BytesExplainMysqlClient(FakeMysqlClient):
    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {
                "EXPLAIN": b'{"query_block":{"table":{"table_name":"orders","access_type":"ALL"}}}'
            }
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


class RecordingMysqlClient(FakeMysqlClient):
    def query_one(self, sql: str):
        self.queries.append(sql)
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {"EXPLAIN": {"query_block": {"table": {"table_name": "orders", "access_type": "ALL"}}}}
        if sql.startswith("SHOW CREATE TABLE"):
            return {"Table": "orders", "Create Table": "CREATE TABLE orders (id bigint primary key)"}
        return {}


def test_collector_uses_explain_format_json_and_show_create_table():
    collector = MysqlEvidenceCollector(FakeMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    evidence = collector.collect("select * from shop.orders", [target])

    assert evidence.explain_json["query_block"]["table"]["access_type"] == "ALL"
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")


def test_collector_parses_bytes_explain_payload():
    collector = MysqlEvidenceCollector(BytesExplainMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    evidence = collector.collect("select * from shop.orders", [target])

    assert evidence.explain_json["query_block"]["table"]["table_name"] == "orders"


def test_collector_escapes_backticks_in_show_create_table_query():
    client = RecordingMysqlClient()
    collector = MysqlEvidenceCollector(client)
    target = MysqlTableTarget(schema_name="sh`op", table_name="or`ders")

    collector.collect("select * from `sh`op`.`or`ders`", [target])

    assert client.queries[1] == "SHOW CREATE TABLE `sh``op`.`or``ders`"


def test_collect_explain_json_is_available_for_partial_collection():
    collector = MysqlEvidenceCollector(FakeMysqlClient())

    explain_json = collector.collect_explain_json("select * from shop.orders")

    assert explain_json["query_block"]["table"]["table_name"] == "orders"


def test_collect_create_tables_is_available_for_partial_collection():
    collector = MysqlEvidenceCollector(FakeMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    create_tables = collector.collect_create_tables([target])

    assert create_tables == {
        "shop.orders": "CREATE TABLE orders (id bigint primary key)"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_mysql_collector.py -v
```

Expected: FAIL because `collect_explain_json` and `collect_create_tables` do not exist yet.

- [ ] **Step 3: Implement granular collection methods**

Update `src/chatdba/db/mysql_collector.py` to:

```python
import json
from typing import Protocol

from pydantic import BaseModel, Field


class MysqlClient(Protocol):
    def query_one(self, sql: str) -> dict[str, object]:
        raise NotImplementedError


class MysqlTableTarget(BaseModel):
    schema_name: str
    table_name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class MysqlEvidence(BaseModel):
    explain_json: dict[str, object]
    create_tables: dict[str, str] = Field(default_factory=dict)


def _parse_explain_payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise TypeError(
                f"Expected EXPLAIN payload to decode to a dict, got {type(parsed).__name__}"
            )
        return parsed
    raise TypeError(
        "Expected EXPLAIN payload as dict, str, bytes, or bytearray; "
        f"got {type(value).__name__}"
    )


def _quote_mysql_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


class MysqlEvidenceCollector:
    def __init__(self, client: MysqlClient) -> None:
        self._client = client

    def collect(self, sql: str, tables: list[MysqlTableTarget]) -> MysqlEvidence:
        return MysqlEvidence(
            explain_json=self.collect_explain_json(sql),
            create_tables=self.collect_create_tables(tables),
        )

    def collect_explain_json(self, sql: str) -> dict[str, object]:
        explain_row = self._client.query_one(f"EXPLAIN FORMAT=JSON {sql}")
        return _parse_explain_payload(explain_row["EXPLAIN"])

    def collect_create_tables(
        self,
        tables: list[MysqlTableTarget],
    ) -> dict[str, str]:
        create_tables: dict[str, str] = {}
        for table in tables:
            row = self._client.query_one(
                "SHOW CREATE TABLE "
                f"{_quote_mysql_identifier(table.schema_name)}."
                f"{_quote_mysql_identifier(table.table_name)}"
            )
            create_tables[table.qualified_name] = str(row["Create Table"])
        return create_tables
```

- [ ] **Step 4: Run collector tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_mysql_collector.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/db/mysql_collector.py tests/unit/test_mysql_collector.py
git commit -m "feat: add granular mysql evidence collection"
```

Expected: commit succeeds.

## Task 3: Metadata Router

**Files:**
- Create: `src/chatdba/db/metadata_router.py`
- Test: `tests/unit/test_metadata_router.py`

- [ ] **Step 1: Write the failing router tests**

Create `tests/unit/test_metadata_router.py`:

```python
from chatdba.db.metadata_router import (
    MetadataRouteRow,
    MetadataRouter,
    MysqlMetadataRouteRepository,
)
from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import EvidenceStatus


class FakeMetadataRouteRepository:
    def __init__(self, rows):
        self.rows = rows
        self.requested = []

    def find_routes(self, tables):
        self.requested.append([(table.schema_name, table.table_name) for table in tables])
        return self.rows


class FakeMetadataMysqlClient:
    def __init__(self):
        self.sql = None
        self.params = None

    def query_all(self, sql, params):
        self.sql = sql
        self.params = params
        return [
            {
                "schema_name": "shop",
                "table_name": "orders",
                "instance_id": "mysql-order-ro",
                "host": "10.0.0.10",
                "port": 3306,
                "readonly_username": "readonly",
                "readonly_password": "secret",
                "default_schema": "shop",
                "db_type": "mysql",
                "version": "8.0",
                "enabled": 1,
            }
        ]


def test_metadata_route_repository_maps_rows_from_metadata_database():
    client = FakeMetadataMysqlClient()
    repository = MysqlMetadataRouteRepository(
        client=client,
        route_table="table_routes",
        instance_table="db_instances",
    )

    rows = repository.find_routes(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert rows[0].instance_id == "mysql-order-ro"
    assert rows[0].readonly_username == "readonly"
    assert "table_routes" in client.sql
    assert client.params == ["shop", "orders"]


def test_router_returns_single_instance_route():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            )
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.FULL
    assert route.route.instance_id == "mysql-order-ro"
    assert route.route.credentials == {"username": "readonly", "password": "secret"}
    assert route.collection_errors == []


def test_router_degrades_when_tables_span_multiple_instances():
    repository = FakeMetadataRouteRepository(
        [
            MetadataRouteRow(
                schema_name="shop",
                table_name="orders",
                instance_id="mysql-order-ro",
                host="10.0.0.10",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="shop",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
            MetadataRouteRow(
                schema_name="crm",
                table_name="customer",
                instance_id="mysql-crm-ro",
                host="10.0.0.20",
                port=3306,
                readonly_username="readonly",
                readonly_password="secret",
                default_schema="crm",
                db_type="mysql",
                version="8.0",
                enabled=True,
            ),
        ]
    )
    router = MetadataRouter(repository)

    route = router.resolve(
        [
            MysqlTableTarget(schema_name="shop", table_name="orders"),
            MysqlTableTarget(schema_name="crm", table_name="customer"),
        ]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert route.missing_evidence == ["route_info", "explain_json", "create_table"]
    assert "multiple source instances" in route.collection_errors[0]


def test_router_degrades_when_table_route_is_missing():
    repository = FakeMetadataRouteRepository([])
    router = MetadataRouter(repository)

    route = router.resolve(
        [MysqlTableTarget(schema_name="shop", table_name="orders")]
    )

    assert route.status == EvidenceStatus.SQL_ONLY
    assert route.route is None
    assert "No metadata route found" in route.collection_errors[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_metadata_router.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.db.metadata_router'`.

- [ ] **Step 3: Implement the metadata router**

Create `src/chatdba/db/metadata_router.py`:

```python
from typing import Protocol

from pydantic import BaseModel

from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute


class MetadataRouteRow(BaseModel):
    schema_name: str
    table_name: str
    instance_id: str
    host: str
    port: int
    readonly_username: str
    readonly_password: str
    default_schema: str | None = None
    db_type: str = "mysql"
    version: str | None = None
    enabled: bool = True


class MetadataMysqlClient(Protocol):
    def query_all(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> list[dict[str, object]]:
        raise NotImplementedError


class MetadataRouteRepository(Protocol):
    def find_routes(
        self,
        tables: list[MysqlTableTarget],
    ) -> list[MetadataRouteRow]:
        raise NotImplementedError


class MysqlMetadataRouteRepository:
    def __init__(
        self,
        *,
        client: MetadataMysqlClient,
        route_table: str,
        instance_table: str,
    ) -> None:
        self._client = client
        self._route_table = route_table
        self._instance_table = instance_table

    def find_routes(
        self,
        tables: list[MysqlTableTarget],
    ) -> list[MetadataRouteRow]:
        if not tables:
            return []

        predicates: list[str] = []
        params: list[object] = []
        for table in tables:
            predicates.append("(r.schema_name = %s AND r.table_name = %s)")
            params.extend([table.schema_name, table.table_name])

        sql = f"""
        SELECT
            r.schema_name,
            r.table_name,
            i.instance_id,
            i.host,
            i.port,
            i.readonly_username,
            i.readonly_password,
            i.default_schema,
            i.db_type,
            i.version,
            i.enabled
        FROM {self._route_table} AS r
        JOIN {self._instance_table} AS i
          ON i.instance_id = r.instance_id
        WHERE {" OR ".join(predicates)}
        """
        return [
            MetadataRouteRow.model_validate(row)
            for row in self._client.query_all(sql, params)
        ]


class MetadataRouter:
    def __init__(self, repository: MetadataRouteRepository) -> None:
        self._repository = repository

    def resolve(self, tables: list[MysqlTableTarget]) -> EvidenceEnvelope:
        rows = self._repository.find_routes(tables)
        if not rows or len(rows) != len(tables):
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["No metadata route found for one or more tables."],
            )

        if any(not row.enabled for row in rows):
            disabled = sorted({row.instance_id for row in rows if not row.enabled})
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    f"Source instance is disabled in metadata routing: {', '.join(disabled)}."
                ],
            )

        instance_ids = {row.instance_id for row in rows}
        if len(instance_ids) != 1:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=[
                    "SQL references multiple source instances and cannot be routed to a single source database."
                ],
            )

        first = rows[0]
        return EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            route=SourceRoute(
                instance_id=first.instance_id,
                db_type=first.db_type,
                version=first.version,
                host=first.host,
                port=first.port,
                default_schema=first.default_schema,
                credentials={
                    "username": first.readonly_username,
                    "password": first.readonly_password,
                },
                schema_names=sorted({row.schema_name for row in rows}),
            ),
        )
```

- [ ] **Step 4: Run router tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_metadata_router.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/db/metadata_router.py tests/unit/test_metadata_router.py
git commit -m "feat: add metadata router"
```

Expected: commit succeeds.

## Task 4: Runtime MySQL Client And Routed Collector

**Files:**
- Create: `src/chatdba/db/runtime_mysql.py`
- Create: `src/chatdba/db/routed_collector.py`
- Test: `tests/unit/test_runtime_mysql.py`
- Test: `tests/unit/test_routed_collector.py`

- [ ] **Step 1: Write the failing runtime/client tests**

Create `tests/unit/test_runtime_mysql.py`:

```python
from chatdba.db.runtime_mysql import MysqlConnectionConfig, RuntimeMysqlClient, SourceMysqlConnectionFactory


def test_connection_factory_builds_runtime_client_from_route():
    route = type(
        "Route",
        (),
        {
            "host": "10.0.0.10",
            "port": 3306,
            "default_schema": "shop",
            "credentials": {
                "username": "readonly",
                "password": "secret",
            },
        },
    )()

    factory = SourceMysqlConnectionFactory(
        connect_timeout_seconds=3,
        query_timeout_seconds=8,
    )

    config = factory.build_config(route)

    assert config.host == "10.0.0.10"
    assert config.port == 3306
    assert config.database == "shop"
    assert config.username == "readonly"
    assert config.password == "secret"


def test_runtime_mysql_client_query_all_returns_dict_rows():
    class FakeCursor:
        def __init__(self):
            self.executed = None

        def execute(self, sql, params=None):
            self.executed = (sql, params)

        def fetchall(self):
            return [{"instance_id": "mysql-order-ro"}]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    connection = FakeConnection()
    client = RuntimeMysqlClient(
        connection_factory=lambda **kwargs: connection,
        config=MysqlConnectionConfig(
            host="127.0.0.1",
            port=3306,
            username="readonly",
            password="secret",
            database="metadata",
            connect_timeout_seconds=3,
            query_timeout_seconds=8,
        ),
    )

    rows = client.query_all(
        "select * from db_instances where instance_id = %s",
        ["mysql-order-ro"],
    )

    assert rows == [{"instance_id": "mysql-order-ro"}]
    assert connection.cursor_obj.executed == (
        "select * from db_instances where instance_id = %s",
        ["mysql-order-ro"],
    )
    assert connection.closed is True
```

Create `tests/unit/test_routed_collector.py`:

```python
from chatdba.db.metadata_router import MetadataRouteRow
from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.domain.models import EvidenceStatus


class FakeRouter:
    def __init__(self, envelope):
        self.envelope = envelope

    def resolve(self, tables):
        return self.envelope


class FakeConnectionFactory:
    def __init__(self, client):
        self.client = client

    def create_client(self, route):
        return self.client


class SuccessfulMysqlClient:
    def query_one(self, sql: str):
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return {
                "EXPLAIN": {
                    "query_block": {
                        "table": {
                            "table_name": "orders",
                            "access_type": "ALL",
                        }
                    }
                }
            }
        return {
            "Create Table": "CREATE TABLE orders (id bigint primary key)"
        }


class ExplainFailingMysqlClient(SuccessfulMysqlClient):
    def query_one(self, sql: str):
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            raise RuntimeError("explain timeout")
        return super().query_one(sql)


def make_full_route_envelope():
    from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute

    return EvidenceEnvelope(
        status=EvidenceStatus.FULL,
        route=SourceRoute(
            instance_id="mysql-order-ro",
            db_type="mysql",
            version="8.0",
            host="10.0.0.10",
            port=3306,
            default_schema="shop",
            credentials={"username": "readonly", "password": "secret"},
            schema_names=["shop"],
        ),
    )


def test_routed_collector_returns_full_evidence_when_source_collection_succeeds():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.FULL
    assert evidence.explain_json["query_block"]["table"]["table_name"] == "orders"
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")
    assert evidence.collection_errors == []


def test_routed_collector_returns_partial_when_explain_fails_but_ddl_succeeds():
    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(make_full_route_envelope()),
        connection_factory=FakeConnectionFactory(ExplainFailingMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.PARTIAL
    assert evidence.explain_json is None
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")
    assert evidence.missing_evidence == ["explain_json"]
    assert "explain timeout" in evidence.collection_errors[0]


def test_routed_collector_preserves_sql_only_when_router_cannot_route():
    from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus

    collector = RoutedMysqlEvidenceCollector(
        router=FakeRouter(
            EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                missing_evidence=["route_info", "explain_json", "create_table"],
                collection_errors=["No metadata route found for one or more tables."],
            )
        ),
        connection_factory=FakeConnectionFactory(SuccessfulMysqlClient()),
    )

    evidence = collector.collect(
        "select * from orders",
        [MysqlTableTarget(schema_name="shop", table_name="orders")],
    )

    assert evidence.status == EvidenceStatus.SQL_ONLY
    assert evidence.route is None
    assert "No metadata route found" in evidence.collection_errors[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_runtime_mysql.py tests/unit/test_routed_collector.py -v
```

Expected: FAIL because the runtime MySQL and routed collector modules do not exist yet.

- [ ] **Step 3: Implement runtime MySQL helpers and routed collector**

Create `src/chatdba/db/runtime_mysql.py`:

```python
from typing import Any

from pydantic import BaseModel


class MysqlConnectionConfig(BaseModel):
    host: str
    port: int
    username: str
    password: str
    database: str
    connect_timeout_seconds: int
    query_timeout_seconds: int


class RuntimeMysqlClient:
    def __init__(
        self,
        connection_factory,
        config: MysqlConnectionConfig,
        *,
        cursorclass: Any | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._config = config
        self._cursorclass = cursorclass

    def _connect(self):
        return self._connection_factory(
            host=self._config.host,
            port=self._config.port,
            user=self._config.username,
            password=self._config.password,
            database=self._config.database,
            connect_timeout=self._config.connect_timeout_seconds,
            read_timeout=self._config.query_timeout_seconds,
            write_timeout=self._config.query_timeout_seconds,
            cursorclass=self._cursorclass,
        )

    def query_one(self, sql: str) -> dict[str, object]:
        rows = self.query_all(sql)
        if not rows:
            raise RuntimeError(f"MySQL query returned no rows: {sql}")
        return rows[0]

    def query_all(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> list[dict[str, object]]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()


class SourceMysqlConnectionFactory:
    def __init__(
        self,
        *,
        connect_timeout_seconds: int,
        query_timeout_seconds: int,
        connection_factory=None,
    ) -> None:
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds
        self._connection_factory = connection_factory

    def build_config(self, route) -> MysqlConnectionConfig:
        return MysqlConnectionConfig(
            host=route.host,
            port=route.port or 3306,
            username=route.credentials["username"],
            password=route.credentials["password"],
            database=route.default_schema or "mysql",
            connect_timeout_seconds=self._connect_timeout_seconds,
            query_timeout_seconds=self._query_timeout_seconds,
        )

    def create_client(self, route) -> RuntimeMysqlClient:
        return RuntimeMysqlClient(
            self._connection_factory,
            self.build_config(route),
        )


def build_metadata_client(settings) -> RuntimeMysqlClient:
    import pymysql

    return RuntimeMysqlClient(
        connection_factory=pymysql.connect,
        config=MysqlConnectionConfig(
            host=settings.metadata_mysql_host,
            port=settings.metadata_mysql_port,
            username=settings.metadata_mysql_user,
            password=settings.metadata_mysql_password,
            database=settings.metadata_mysql_database,
            connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
            query_timeout_seconds=settings.mysql_query_timeout_seconds,
        ),
        cursorclass=pymysql.cursors.DictCursor,
    )
```

Create `src/chatdba/db/routed_collector.py`:

```python
from chatdba.db.metadata_router import MetadataRouter
from chatdba.db.mysql_collector import MysqlEvidenceCollector, MysqlTableTarget
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus


class RoutedMysqlEvidenceCollector:
    def __init__(self, *, router: MetadataRouter, connection_factory) -> None:
        self._router = router
        self._connection_factory = connection_factory

    def collect(
        self,
        sql: str,
        tables: list[MysqlTableTarget],
    ) -> EvidenceEnvelope:
        route_envelope = self._router.resolve(tables)
        if route_envelope.route is None:
            return route_envelope

        client = self._connection_factory.create_client(route_envelope.route)
        collector = MysqlEvidenceCollector(client)

        explain_json = None
        create_tables: dict[str, str] = {}
        missing_evidence: list[str] = []
        collection_errors: list[str] = []

        try:
            explain_json = collector.collect_explain_json(sql)
        except Exception as exc:
            missing_evidence.append("explain_json")
            collection_errors.append(f"Failed to collect execution plan: {exc}")

        try:
            create_tables = collector.collect_create_tables(tables)
        except Exception as exc:
            missing_evidence.append("create_table")
            collection_errors.append(f"Failed to collect table DDL: {exc}")

        if explain_json is not None and create_tables:
            return EvidenceEnvelope(
                status=EvidenceStatus.FULL,
                route=route_envelope.route,
                explain_json=explain_json,
                create_tables=create_tables,
            )

        if explain_json is None and not create_tables:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                route=route_envelope.route,
                explain_json=None,
                create_tables={},
                missing_evidence=sorted(set(missing_evidence)),
                collection_errors=collection_errors,
            )

        return EvidenceEnvelope(
            status=EvidenceStatus.PARTIAL,
            route=route_envelope.route,
            explain_json=explain_json,
            create_tables=create_tables,
            missing_evidence=sorted(set(missing_evidence)),
            collection_errors=collection_errors,
        )
```

- [ ] **Step 4: Run runtime/client tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_runtime_mysql.py tests/unit/test_routed_collector.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/db/runtime_mysql.py src/chatdba/db/routed_collector.py tests/unit/test_runtime_mysql.py tests/unit/test_routed_collector.py
git commit -m "feat: add routed mysql evidence collection"
```

Expected: commit succeeds.

## Task 5: Report Builder And Workflow Degradation

**Files:**
- Create: `src/chatdba/workflow/report_builder.py`
- Modify: `src/chatdba/models/qwen_gateway.py`
- Modify: `src/chatdba/workflow/state.py`
- Modify: `src/chatdba/workflow/sql_optimization.py`
- Test: `tests/unit/test_report_builder.py`
- Test: `tests/unit/test_qwen_gateway.py`
- Test: `tests/integration/test_workflow_happy_path.py`

- [ ] **Step 1: Write the failing report/workflow tests**

Create `tests/unit/test_report_builder.py`:

```python
from chatdba.cases.repository import OptimizationCase
from chatdba.domain.models import ConfidenceLabel, EvidenceEnvelope, EvidenceStatus, RuleFinding, SqlFeatures
from chatdba.workflow.report_builder import OptimizationReportComposer


def test_report_builder_creates_sql_only_report_without_qwen():
    composer = OptimizationReportComposer(cases=[])

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from orders order by created_at desc limit 20",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            order_by=["created_at DESC"],
            has_limit=True,
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=["No metadata route found for one or more tables."],
        ),
        findings=[
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="ORDER BY with LIMIT may require a supporting index.",
                evidence={"order_by": ["created_at DESC"]},
            )
        ],
    )

    assert report.task_id == "task-1"
    assert report.evidence_status == EvidenceStatus.SQL_ONLY
    assert report.confidence_label == ConfidenceLabel.LOW
    assert "No source execution evidence was available." in report.limitations[0]


def test_report_builder_uses_cases_and_qwen_json_when_available():
    class FakeQwenGateway:
        def generate_report(self, system_prompt: str, user_prompt: str) -> str:
            assert "SQL优化报告" in system_prompt
            assert "filesort fixed" in user_prompt
            return """
            {
              "task_id": "task-1",
              "summary": "Use an index to avoid filesort.",
              "confidence": 0.78,
              "confidence_label": "medium",
              "evidence_status": "partial",
              "missing_evidence": ["create_table"],
              "limitations": ["DDL could not be collected."],
              "bottlenecks": [{"code": "full_table_scan", "evidence": "rows examined is high"}],
              "sql_rewrites": [{"title": "Rewrite", "sql": "select * from orders"}],
              "index_recommendations": [{"ddl": "create index idx_orders_created_at on orders(created_at)", "risk": "medium"}],
              "risks": [{"level": "medium", "description": "Review online DDL strategy."}],
              "validation_steps": ["Run EXPLAIN FORMAT=JSON again after creating the index."],
              "similar_cases": [{"case_id": "case-1", "reason": "same filesort symptom"}]
            }
            """

    composer = OptimizationReportComposer(
        qwen_gateway=FakeQwenGateway(),
        cases=[
            OptimizationCase(
                case_id="case-1",
                db_type="mysql",
                scenario_tags=["order_by"],
                case_card="filesort fixed",
                quality_score=0.9,
            )
        ],
    )

    report = composer.compose(
        task_id="task-1",
        raw_sql="select * from orders order by created_at desc limit 20",
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            order_by=["created_at DESC"],
            has_limit=True,
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.PARTIAL,
            missing_evidence=["create_table"],
            collection_errors=["Failed to collect table DDL: timeout"],
        ),
        findings=[],
    )

    assert report.summary == "Use an index to avoid filesort."
    assert report.similar_cases[0].case_id == "case-1"
```

Update `tests/unit/test_qwen_gateway.py` to:

```python
from chatdba.models.qwen_gateway import QwenGateway


class FakeChunk:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": content})()})]


class FakeStreamCompletions:
    def create(self, **kwargs):
        assert kwargs["model"] == "qwen-plus"
        assert kwargs["messages"] == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        assert kwargs["stream"] is True
        return [FakeChunk("hello"), FakeChunk(""), FakeChunk(" world")]


class FakeNonStreamChoice:
    def __init__(self, content: str):
        self.message = type("Message", (), {"content": content})()


class FakeNonStreamCompletions:
    def create(self, **kwargs):
        assert kwargs["stream"] is False
        return type("Response", (), {"choices": [FakeNonStreamChoice("{\"ok\": true}")]} )()


class FakeClient:
    chat = type("Chat", (), {"completions": FakeStreamCompletions()})()


class FakeNonStreamClient:
    chat = type("Chat", (), {"completions": FakeNonStreamCompletions()})()


def test_gateway_streams_text_chunks():
    gateway = QwenGateway(client=FakeClient(), model="qwen-plus")

    assert list(gateway.stream_report("system", "user")) == ["hello", " world"]


def test_gateway_generates_non_stream_report_text():
    gateway = QwenGateway(client=FakeNonStreamClient(), model="qwen-plus")

    assert gateway.generate_report("system", "user") == "{\"ok\": true}"
```

Update `tests/integration/test_workflow_happy_path.py` to:

```python
from chatdba.domain.models import ConfidenceLabel, EvidenceEnvelope, EvidenceStatus
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


class FakeCollector:
    def collect(self, sql, tables):
        return EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            explain_json={
                "query_block": {
                    "table": {
                        "table_name": "orders",
                        "access_type": "ALL",
                        "rows_examined_per_scan": 20000,
                    }
                }
            },
            create_tables={"shop.orders": "CREATE TABLE orders (id bigint primary key)"},
        )


def test_workflow_returns_report_payload():
    graph = build_sql_optimization_graph(
        collector=FakeCollector(),
        report_composer=OptimizationReportComposer(cases=[]),
    )

    result = graph.invoke(
        {
            "task_id": "task-1",
            "raw_sql": "select * from orders",
            "default_schema": "shop",
        }
    )

    assert result["task_id"] == "task-1"
    assert result["findings"][0].code == "full_table_scan"
    assert result["report"].task_id == "task-1"
    assert result["report"].evidence_status == EvidenceStatus.FULL
    assert result["report"].confidence_label == ConfidenceLabel.HIGH
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_report_builder.py tests/unit/test_qwen_gateway.py tests/integration/test_workflow_happy_path.py -v
```

Expected: FAIL because the report builder and report-aware workflow do not exist yet.

- [ ] **Step 3: Implement report composition and degraded workflow**

Create `src/chatdba/workflow/report_builder.py`:

```python
import json
from typing import Protocol

from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases
from chatdba.domain.models import ConfidenceLabel, EvidenceEnvelope, EvidenceStatus, RuleFinding, SqlFeatures
from chatdba.domain.report_schema import (
    Bottleneck,
    OptimizationReport,
    Risk,
    SimilarCase,
)


class QwenReportGateway(Protocol):
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class OptimizationReportComposer:
    def __init__(
        self,
        *,
        qwen_gateway: QwenReportGateway | None = None,
        cases: list[OptimizationCase] | None = None,
    ) -> None:
        self._qwen_gateway = qwen_gateway
        self._cases = cases or []

    def compose(
        self,
        *,
        task_id: str,
        raw_sql: str,
        sql_features: SqlFeatures,
        evidence: EvidenceEnvelope,
        findings: list[RuleFinding],
    ) -> OptimizationReport:
        scenario_tags = _scenario_tags(sql_features, findings)
        similar_cases = retrieve_cases(
            self._cases,
            db_type="mysql",
            scenario_tags=scenario_tags,
            limit=3,
        )
        if self._qwen_gateway is not None:
            system_prompt = (
                "你是 ChatDBA，请根据输入证据生成 SQL优化报告。"
                "输出必须是合法 JSON，并符合给定 schema。"
            )
            user_prompt = json.dumps(
                {
                    "task_id": task_id,
                    "raw_sql": raw_sql,
                    "sql_features": sql_features.model_dump(mode="json"),
                    "evidence": evidence.model_dump(mode="json"),
                    "findings": [finding.model_dump(mode="json") for finding in findings],
                    "similar_cases": [case.model_dump(mode="json") for case in similar_cases],
                },
                ensure_ascii=False,
            )
            content = self._qwen_gateway.generate_report(system_prompt, user_prompt)
            report = OptimizationReport.model_validate_json(content)
            return report

        confidence_label, confidence = _confidence_for(evidence.status)
        limitations = _limitations_for(evidence)
        risks = [Risk(level="medium", description=message) for message in evidence.collection_errors]
        bottlenecks = [
            Bottleneck(code=finding.code, evidence=finding.message)
            for finding in findings
        ]
        return OptimizationReport(
            task_id=task_id,
            summary=_summary_for(evidence.status, findings),
            confidence=confidence,
            confidence_label=confidence_label,
            evidence_status=evidence.status,
            missing_evidence=evidence.missing_evidence,
            limitations=limitations,
            bottlenecks=bottlenecks,
            sql_rewrites=[],
            index_recommendations=[],
            risks=risks,
            validation_steps=_validation_steps_for(evidence.status),
            similar_cases=[
                SimilarCase(case_id=case.case_id, reason=case.case_card)
                for case in similar_cases
            ],
        )


def _confidence_for(status: EvidenceStatus) -> tuple[ConfidenceLabel, float]:
    if status == EvidenceStatus.FULL:
        return ConfidenceLabel.HIGH, 0.85
    if status == EvidenceStatus.PARTIAL:
        return ConfidenceLabel.MEDIUM, 0.6
    return ConfidenceLabel.LOW, 0.35


def _summary_for(status: EvidenceStatus, findings: list[RuleFinding]) -> str:
    if findings:
        return findings[0].message
    if status == EvidenceStatus.FULL:
        return "Collected full source evidence and generated optimization guidance."
    if status == EvidenceStatus.PARTIAL:
        return "Collected partial source evidence and generated optimization guidance."
    return "Generated SQL-level optimization guidance without source execution evidence."


def _limitations_for(evidence: EvidenceEnvelope) -> list[str]:
    if evidence.status == EvidenceStatus.FULL:
        return []
    if evidence.status == EvidenceStatus.PARTIAL:
        return ["Some conclusions are inferred because source evidence is incomplete."]
    return ["No source execution evidence was available."]


def _validation_steps_for(status: EvidenceStatus) -> list[str]:
    if status == EvidenceStatus.FULL:
        return ["Verify recommendations on the target source database with EXPLAIN FORMAT=JSON."]
    if status == EvidenceStatus.PARTIAL:
        return ["Re-run the missing source evidence collection before applying high-risk changes."]
    return ["Validate the SQL against the target source database before applying any recommendation."]


def _scenario_tags(
    sql_features: SqlFeatures,
    findings: list[RuleFinding],
) -> list[str]:
    tags: list[str] = []
    if sql_features.order_by:
        tags.append("order_by")
    if sql_features.joins:
        tags.append("join")
    if any(finding.code == "full_table_scan" for finding in findings):
        tags.append("full_table_scan")
    return tags
```

Update `src/chatdba/models/qwen_gateway.py` to:

```python
from collections.abc import Iterator

from openai import OpenAI


class QwenGateway:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def stream_report(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        return str(response.choices[0].message.content or "")
```

Update `src/chatdba/workflow/state.py` to:

```python
from typing import TypedDict

from chatdba.domain.models import EvidenceEnvelope, RuleFinding, SqlFeatures
from chatdba.domain.report_schema import OptimizationReport


class SqlOptimizationState(TypedDict, total=False):
    task_id: str
    raw_sql: str
    default_schema: str
    sql_features: SqlFeatures
    evidence: EvidenceEnvelope
    findings: list[RuleFinding]
    report: OptimizationReport
```

Update `src/chatdba/workflow/sql_optimization.py` to:

```python
from langgraph.graph import END, StateGraph

from chatdba.db.metadata_repository import StaticMetadataRepository
from chatdba.explain.mysql_json import extract_plan_features
from chatdba.rules.mysql_rules import run_mysql_rules
from chatdba.sql.parser import parse_sql_features
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.workflow.state import SqlOptimizationState


def build_sql_optimization_graph(collector, report_composer: OptimizationReportComposer | None = None):
    graph = StateGraph(SqlOptimizationState)
    composer = report_composer or OptimizationReportComposer(cases=[])

    def parse_sql(state: SqlOptimizationState) -> SqlOptimizationState:
        return {"sql_features": parse_sql_features(state["raw_sql"])}

    def collect_evidence(state: SqlOptimizationState) -> SqlOptimizationState:
        sql_features = state["sql_features"]
        resolver = StaticMetadataRepository(
            default_schema=state.get("default_schema", "default")
        )
        targets = resolver.resolve_tables(sql_features.tables)
        return {"evidence": collector.collect(state["raw_sql"], targets)}

    def diagnose(state: SqlOptimizationState) -> SqlOptimizationState:
        explain_json = state["evidence"].explain_json or {}
        plan_features = extract_plan_features(explain_json) if explain_json else []
        findings = run_mysql_rules(state["sql_features"], plan_features)
        return {"findings": findings}

    def build_report(state: SqlOptimizationState) -> SqlOptimizationState:
        report = composer.compose(
            task_id=state["task_id"],
            raw_sql=state["raw_sql"],
            sql_features=state["sql_features"],
            evidence=state["evidence"],
            findings=state["findings"],
        )
        return {"report": report}

    graph.add_node("parse_sql", parse_sql)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("diagnose", diagnose)
    graph.add_node("build_report", build_report)
    graph.set_entry_point("parse_sql")
    graph.add_edge("parse_sql", "collect_evidence")
    graph.add_edge("collect_evidence", "diagnose")
    graph.add_edge("diagnose", "build_report")
    graph.add_edge("build_report", END)
    return graph.compile()
```

- [ ] **Step 4: Run report/workflow tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_report_builder.py tests/unit/test_qwen_gateway.py tests/integration/test_workflow_happy_path.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/workflow/report_builder.py src/chatdba/models/qwen_gateway.py src/chatdba/workflow/state.py src/chatdba/workflow/sql_optimization.py tests/unit/test_report_builder.py tests/unit/test_qwen_gateway.py tests/integration/test_workflow_happy_path.py
git commit -m "feat: add degraded optimization report workflow"
```

Expected: commit succeeds.

## Task 6: Runtime Wiring And DingTalk Report Rendering

**Files:**
- Create: `src/chatdba/dingtalk/rendering.py`
- Modify: `src/chatdba/worker/run_task.py`
- Modify: `src/chatdba/tasks/service.py`
- Modify: `src/chatdba/dingtalk/handler.py`
- Modify: `src/chatdba/dingtalk/runtime.py`
- Modify: `src/chatdba/config/settings.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/unit/test_worker_run_task.py`
- Test: `tests/unit/test_optimization_task_service.py`
- Test: `tests/unit/test_dingtalk_handler.py`
- Test: `tests/unit/test_dingtalk_runtime_builder.py`
- Test: `tests/integration/test_dingtalk_e2e_flow.py`

- [ ] **Step 1: Write the failing runtime/handler tests**

Update `tests/unit/test_worker_run_task.py` to:

```python
from chatdba.worker.run_task import run_sql_optimization_task


def test_run_sql_optimization_task_invokes_graph_with_collector(monkeypatch):
    seen = {}

    class FakeGraph:
        def invoke(self, payload):
            seen["payload"] = payload
            return {"result": "ok"}

    def fake_build_sql_optimization_graph(*, collector, report_composer=None):
        seen["collector"] = collector
        seen["report_composer"] = report_composer
        return FakeGraph()

    monkeypatch.setattr(
        "chatdba.worker.run_task.build_sql_optimization_graph",
        fake_build_sql_optimization_graph,
    )

    collector = object()
    report_composer = object()
    task_payload = {"raw_sql": "select * from orders"}

    result = run_sql_optimization_task(
        task_payload,
        collector,
        report_composer=report_composer,
    )

    assert result == {"result": "ok"}
    assert seen["collector"] is collector
    assert seen["report_composer"] is report_composer
    assert seen["payload"] == task_payload


def test_run_sql_optimization_task_emits_progress(monkeypatch):
    class FakeGraph:
        def invoke(self, payload):
            return {"result": "ok", "payload": payload}

    def fake_build_sql_optimization_graph(*, collector, report_composer=None):
        return FakeGraph()

    monkeypatch.setattr(
        "chatdba.worker.run_task.build_sql_optimization_graph",
        fake_build_sql_optimization_graph,
    )

    events = []

    result = run_sql_optimization_task(
        {"raw_sql": "select * from orders"},
        object(),
        progress_sink=events.append,
    )

    assert result["result"] == "ok"
    assert events == [
        "Parsing SQL\n",
        "Generated diagnostic findings\n",
        "Built optimization report\n",
    ]
```

Update `tests/unit/test_optimization_task_service.py` to:

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

    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        seen["task_payload"] = task_payload
        seen["collector"] = collector
        seen["report_composer"] = report_composer
        seen["progress_sink"] = progress_sink
        if progress_sink:
            progress_sink("Parsing SQL\n")
        return {"report": {"summary": "ok"}}

    progress = []
    progress_sink = progress.append
    collector = object()
    report_composer = object()
    service = OptimizationTaskService(
        collector=collector,
        report_composer=report_composer,
        task_runner=fake_runner,
        task_id_factory=lambda: "task-1",
    )

    execution = service.run_sql(
        raw_sql="select * from orders",
        dingtalk_context=make_context(),
        progress_sink=progress_sink,
    )

    assert execution.task_id == "task-1"
    assert execution.status == TaskStatus.COMPLETED
    assert execution.result == {"report": {"summary": "ok"}}
    assert seen["collector"] is collector
    assert seen["report_composer"] is report_composer
```

Update `tests/unit/test_dingtalk_handler.py` to:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import (
    SQL_OPTIMIZATION_FAILED_MESSAGE_PREFIX,
    SQL_OPTIMIZATION_STARTED_MESSAGE,
    SQL_OPTIMIZATION_USAGE_MESSAGE,
    DingTalkSqlOptimizationHandler,
)
from chatdba.dingtalk.responder import DingTalkSendResult
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus, TaskStatus
from chatdba.domain.report_schema import OptimizationReport
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
            result={
                "report": OptimizationReport.model_validate(
                    {
                        "task_id": "task-1",
                        "summary": "Use an index to avoid filesort.",
                        "confidence": 0.35,
                        "confidence_label": "low",
                        "evidence_status": "sql_only",
                        "missing_evidence": ["route_info", "explain_json", "create_table"],
                        "limitations": ["No source execution evidence was available."],
                        "bottlenecks": [{"code": "limit_with_order_by", "evidence": "ORDER BY with LIMIT may require a supporting index."}],
                        "sql_rewrites": [],
                        "index_recommendations": [],
                        "risks": [],
                        "validation_steps": ["Validate the SQL against the target source database before applying any recommendation."],
                        "similar_cases": [],
                    }
                )
            },
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


def test_handler_runs_task_and_sends_start_progress_and_report():
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
    assert responder.messages[0] == SQL_OPTIMIZATION_STARTED_MESSAGE
    assert "Evidence: SQL_ONLY" in responder.messages[-1]
    assert "Use an index to avoid filesort." in responder.messages[-1]


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

Update `tests/unit/test_dingtalk_runtime_builder.py` to:

```python
import asyncio
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.runtime import build_dingtalk_runtime
from chatdba.dingtalk.sdk_runtime import DingTalkSdkBundle


class FakeCredential:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret


class FakeClient:
    def __init__(self, credential):
        self.credential = credential
        self.registrations = []
        self.started = False

    def register_callback_handler(self, topic, handler):
        self.registrations.append((topic, handler))

    def start_forever(self):
        self.started = True


class FakeAckMessage:
    STATUS_OK = "OK"


class FakeChatbotHandler:
    pass


class FakeChatbotMessage:
    TOPIC = "/v1.0/im/bot/messages/get"


class FakeSender:
    def __init__(self):
        self.messages = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.messages.append(text)


def make_sdk_bundle() -> DingTalkSdkBundle:
    stream_module = SimpleNamespace(
        Credential=FakeCredential,
        DingTalkStreamClient=FakeClient,
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=FakeChatbotMessage)
    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


def make_settings():
    return SimpleNamespace(
        dingtalk_client_id="client-id",
        dingtalk_client_secret="client-secret",
        stream_update_interval_ms=1000,
        mysql_connect_timeout_seconds=3,
        mysql_query_timeout_seconds=8,
        metadata_mysql_host="",
        metadata_mysql_port=3306,
        metadata_mysql_user="",
        metadata_mysql_password="",
        metadata_mysql_database="",
        metadata_route_table="table_routes",
        metadata_instance_table="db_instances",
        qwen_api_key="",
        qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        qwen_model="qwen-plus",
    )


def test_build_runtime_registers_chatbot_handler_and_starts_client():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    assert runtime.client.registrations[0][0] == FakeChatbotMessage.TOPIC

    runtime.start()

    assert runtime.client.started is True


def test_build_runtime_uses_routed_collector_when_metadata_settings_are_present(monkeypatch):
    seen = {}
    settings = make_settings()
    settings.metadata_mysql_host = "127.0.0.1"
    settings.metadata_mysql_user = "metadata_ro"
    settings.metadata_mysql_password = "secret"
    settings.metadata_mysql_database = "metadata"

    class FakeRoutedCollector:
        def __init__(self, *, router, connection_factory):
            seen["router"] = router
            seen["connection_factory"] = connection_factory

    monkeypatch.setattr(
        "chatdba.dingtalk.runtime.build_metadata_client",
        lambda settings: "metadata-client",
    )
    monkeypatch.setattr(
        "chatdba.dingtalk.runtime.RoutedMysqlEvidenceCollector",
        FakeRoutedCollector,
    )

    runtime = build_dingtalk_runtime(
        settings=settings,
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    assert type(runtime.collector).__name__ == "FakeRoutedCollector"
    assert seen["router"].__class__.__name__ == "MetadataRouter"


def test_registered_sdk_callback_handler_acks_even_when_routing_is_not_configured():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )
    callback_handler = runtime.client.registrations[0][1]

    status, message = asyncio.run(
        callback_handler.process(
            SimpleNamespace(
                data={
                    "msgId": "msg-1",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "sessionWebhook": "https://example.test/webhook",
                    "msgtype": "text",
                    "text": {"content": "SQL优化 select * from orders"},
                }
            )
        )
    )

    assert status == "OK"
    assert message == "OK"
```

Update `tests/integration/test_dingtalk_e2e_flow.py` to:

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.handler import SQL_OPTIMIZATION_STARTED_MESSAGE, DingTalkSqlOptimizationHandler
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime
from chatdba.domain.models import ConfidenceLabel, EvidenceStatus, TaskStatus
from chatdba.domain.report_schema import OptimizationReport
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


def test_dingtalk_runtime_runs_sql_optimization_and_streams_report():
    def fake_runner(task_payload, collector, report_composer=None, progress_sink=None):
        if progress_sink:
            progress_sink("Parsing SQL\n")
            progress_sink("Built optimization report\n")
        return {
            "report": OptimizationReport.model_validate(
                {
                    "task_id": "task-1",
                    "summary": "Use an index to avoid filesort.",
                    "confidence": 0.35,
                    "confidence_label": "low",
                    "evidence_status": "sql_only",
                    "missing_evidence": ["route_info", "explain_json", "create_table"],
                    "limitations": ["No source execution evidence was available."],
                    "bottlenecks": [{"code": "limit_with_order_by", "evidence": "ORDER BY with LIMIT may require a supporting index."}],
                    "sql_rewrites": [],
                    "index_recommendations": [],
                    "risks": [],
                    "validation_steps": ["Validate the SQL against the target source database before applying any recommendation."],
                    "similar_cases": [],
                }
            )
        }

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
    assert [message["text"] for message in sender.messages][0] == SQL_OPTIMIZATION_STARTED_MESSAGE
    assert "Evidence: SQL_ONLY" in sender.messages[-1]["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_worker_run_task.py tests/unit/test_optimization_task_service.py tests/unit/test_dingtalk_handler.py tests/unit/test_dingtalk_runtime_builder.py tests/integration/test_dingtalk_e2e_flow.py -v
```

Expected: FAIL because report-aware runtime wiring and rendering are not implemented yet.

- [ ] **Step 3: Implement runtime wiring and DingTalk report rendering**

Create `src/chatdba/dingtalk/rendering.py`:

```python
from chatdba.domain.report_schema import OptimizationReport


def render_report_for_dingtalk(report: OptimizationReport) -> str:
    lines = [
        "SQL Optimization Report",
        f"Task: {report.task_id}",
        f"Evidence: {report.evidence_status.upper()}",
        f"Confidence: {report.confidence_label.upper()} ({report.confidence:.2f})",
        f"Summary: {report.summary}",
    ]
    if report.missing_evidence:
        lines.append("Missing: " + ", ".join(report.missing_evidence))
    if report.limitations:
        lines.append("Limitations: " + " | ".join(report.limitations))
    if report.validation_steps:
        lines.append("Validate: " + " | ".join(report.validation_steps))
    return "\n".join(lines)
```

Update `src/chatdba/worker/run_task.py` to:

```python
from collections.abc import Callable

from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


ProgressSink = Callable[[str], None]


def run_sql_optimization_task(
    task_payload: dict[str, object],
    collector,
    report_composer: OptimizationReportComposer | None = None,
    progress_sink: ProgressSink | None = None,
) -> dict[str, object]:
    if progress_sink:
        progress_sink("Parsing SQL\n")
    graph = build_sql_optimization_graph(
        collector=collector,
        report_composer=report_composer,
    )
    result = graph.invoke(task_payload)
    if progress_sink:
        progress_sink("Generated diagnostic findings\n")
        progress_sink("Built optimization report\n")
    return result
```

Update `src/chatdba/tasks/service.py` to:

```python
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
```

Update `src/chatdba/dingtalk/handler.py` to:

```python
from dataclasses import dataclass, field
from typing import Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message
from chatdba.dingtalk.progress import StreamingProgressBridge
from chatdba.dingtalk.rendering import render_report_for_dingtalk
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
            send_results.append(
                self._responder.reply_text(message, SQL_OPTIMIZATION_USAGE_MESSAGE)
            )
            return DingTalkHandleResult(
                accepted=False,
                status=TaskStatus.FAILED,
                error="empty sql",
                send_results=send_results,
            )

        send_results.append(
            self._responder.reply_text(message, SQL_OPTIMIZATION_STARTED_MESSAGE)
        )
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

        execution = self._task_service.run_sql(
            raw_sql=raw_sql,
            dingtalk_context=dingtalk_context,
            progress_sink=bridge.emit,
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

        report = execution.result["report"]
        send_results.append(
            self._responder.reply_text(message, render_report_for_dingtalk(report))
        )
        return DingTalkHandleResult(
            accepted=True,
            task_id=execution.task_id,
            status=TaskStatus.COMPLETED,
            send_results=send_results,
        )
```

Update `src/chatdba/config/settings.py` to:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = Field(default="", repr=False)
    qwen_model: str = "qwen-plus"
    qwen_fallback_model: str = "qwen-max"
    qwen_embedding_model: str = "text-embedding-v4"
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = Field(default="", repr=False)
    dingtalk_stream_enabled: bool = False
    mysql_connect_timeout_seconds: int = 3
    mysql_query_timeout_seconds: int = 8
    stream_update_interval_ms: int = 1000
    metadata_mysql_host: str = ""
    metadata_mysql_port: int = 3306
    metadata_mysql_user: str = ""
    metadata_mysql_password: str = Field(default="", repr=False)
    metadata_mysql_database: str = ""
    metadata_route_table: str = "table_routes"
    metadata_instance_table: str = "db_instances"
```

Update `.env.example` by appending:

```text
METADATA_MYSQL_HOST=
METADATA_MYSQL_PORT=3306
METADATA_MYSQL_USER=
METADATA_MYSQL_PASSWORD=
METADATA_MYSQL_DATABASE=
METADATA_ROUTE_TABLE=table_routes
METADATA_INSTANCE_TABLE=db_instances
```

Update `src/chatdba/dingtalk/runtime.py` to:

```python
from dataclasses import dataclass
from typing import Any

import pymysql
from openai import OpenAI

from chatdba.db.metadata_router import MetadataRouter, MysqlMetadataRouteRepository
from chatdba.db.routed_collector import RoutedMysqlEvidenceCollector
from chatdba.db.runtime_mysql import SourceMysqlConnectionFactory, build_metadata_client
from chatdba.dingtalk.handler import DingTalkSqlOptimizationHandler
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkBundle,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.dingtalk.sender import DingTalkSessionWebhookSender
from chatdba.models.qwen_gateway import QwenGateway
from chatdba.tasks.service import OptimizationTaskService
from chatdba.workflow.report_builder import OptimizationReportComposer


class SqlOnlyCollector:
    def collect(self, sql: str, tables: list[object]):
        from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus

        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=["Metadata routing is not configured for this runtime."],
        )


@dataclass(frozen=True)
class DingTalkSdkRuntime:
    client: Any
    callback_handler: Any
    app_handler: DingTalkSqlOptimizationHandler
    collector: object
    sender: object

    def start(self) -> None:
        self.client.start_forever()


def build_dingtalk_runtime(
    *,
    settings,
    collector: object | None = None,
    sender: object | None = None,
    sdk_bundle: DingTalkSdkBundle | None = None,
) -> DingTalkSdkRuntime:
    bundle = sdk_bundle or load_dingtalk_stream_sdk()
    runtime_collector = collector or SqlOnlyCollector()
    runtime_sender = sender or DingTalkSessionWebhookSender()

    if collector is None and settings.metadata_mysql_host and settings.metadata_mysql_user and settings.metadata_mysql_database:
        metadata_client = build_metadata_client(settings)
        router = MetadataRouter(
            MysqlMetadataRouteRepository(
                client=metadata_client,
                route_table=settings.metadata_route_table,
                instance_table=settings.metadata_instance_table,
            )
        )
        runtime_collector = RoutedMysqlEvidenceCollector(
            router=router,
            connection_factory=SourceMysqlConnectionFactory(
                connect_timeout_seconds=settings.mysql_connect_timeout_seconds,
                query_timeout_seconds=settings.mysql_query_timeout_seconds,
                connection_factory=pymysql.connect,
            ),
        )

    responder = DingTalkResponder(runtime_sender)
    qwen_gateway = None
    if settings.qwen_api_key:
        qwen_gateway = QwenGateway(
            client=OpenAI(
                base_url=settings.qwen_base_url,
                api_key=settings.qwen_api_key,
            ),
            model=settings.qwen_model,
        )
    report_composer = OptimizationReportComposer(
        qwen_gateway=qwen_gateway,
        cases=[],
    )
    task_service = OptimizationTaskService(
        collector=runtime_collector,
        report_composer=report_composer,
    )
    app_handler = DingTalkSqlOptimizationHandler(
        task_service=task_service,
        responder=responder,
        stream_interval_ms=settings.stream_update_interval_ms,
    )
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=bundle,
        adapter=adapter,
    )

    credential = bundle.stream_module.Credential(
        settings.dingtalk_client_id,
        settings.dingtalk_client_secret,
    )
    client = bundle.stream_module.DingTalkStreamClient(credential)
    client.register_callback_handler(
        bundle.chatbot_module.ChatbotMessage.TOPIC,
        callback_handler,
    )
    return DingTalkSdkRuntime(
        client=client,
        callback_handler=callback_handler,
        app_handler=app_handler,
        collector=runtime_collector,
        sender=runtime_sender,
    )


def create_sdk_callback_handler(
    *,
    bundle: DingTalkSdkBundle,
    adapter: DingTalkStreamChatbotHandler,
):
    class CallbackHandler(bundle.stream_module.ChatbotHandler):
        async def process(self, callback):
            adapter.handle_callback_data(callback.data)
            return bundle.stream_module.AckMessage.STATUS_OK, "OK"

    return CallbackHandler()
```

Update `README.md` by appending:

````markdown
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
````

- [ ] **Step 4: Run runtime/handler tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_worker_run_task.py tests/unit/test_optimization_task_service.py tests/unit/test_dingtalk_handler.py tests/unit/test_dingtalk_runtime_builder.py tests/integration/test_dingtalk_e2e_flow.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/rendering.py src/chatdba/worker/run_task.py src/chatdba/tasks/service.py src/chatdba/dingtalk/handler.py src/chatdba/dingtalk/runtime.py src/chatdba/config/settings.py .env.example README.md tests/unit/test_worker_run_task.py tests/unit/test_optimization_task_service.py tests/unit/test_dingtalk_handler.py tests/unit/test_dingtalk_runtime_builder.py tests/integration/test_dingtalk_e2e_flow.py
git commit -m "feat: wire metadata routing into dingtalk runtime"
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

Expected: shows the metadata routing design, plan, and implementation commits.

## Self-Review Checklist

Spec coverage:

- Metadata routing is covered by Task 3.
- Source MySQL collection is covered by Tasks 2 and 4.
- Evidence degradation is covered by Tasks 3, 4, and 5.
- Report evidence status and confidence labeling are covered by Tasks 1 and 5.
- DingTalk final report delivery is covered by Task 6.

Type consistency:

- `EvidenceStatus`, `ConfidenceLabel`, `SourceRoute`, and `EvidenceEnvelope` are defined in Task 1 and reused later.
- `OptimizationReport` is extended in Task 1 and reused by Tasks 5 and 6.
- `MetadataRouteRow` and `MetadataRouter` are defined in Task 3 and reused by Task 4.
- `OptimizationReportComposer` is defined in Task 5 and reused by Task 6.

Execution order:

- Task 1 must run before Task 5 because the report schema and evidence enums are used throughout the workflow.
- Task 2 must run before Task 4 because routed collection depends on granular explain/DDLs methods.
- Task 3 must run before Task 4 because routed collection depends on routing.
- Task 5 must run before Task 6 because the DingTalk handler needs a final report to render.
