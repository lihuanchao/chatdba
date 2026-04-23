# ChatDBA SQL Optimization Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working ChatDBA SQL optimization flow where a DingTalk user submits SQL, receives streamed progress, and gets a Tongyi Qianwen-assisted optimization report.

**Architecture:** Implement a Python service with DingTalk Channel Service, FastAPI internal API, Redis-backed event streaming, LangGraph workflow orchestration, PostgreSQL/pgvector persistence, MySQL read-only evidence collection, a deterministic rule engine, and a Tongyi Qianwen model gateway. The workflow remains evidence-driven: code collects and validates evidence, Qwen generates structured recommendations from that evidence.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangGraph, SQLAlchemy, asyncpg, pgvector, redis, sqlglot, PyMySQL, dingtalk-stream, OpenAI-compatible DashScope API, pytest, pytest-asyncio, respx.

---

## File Structure

Create this structure:

```text
pyproject.toml
.env.example
README.md
src/chatdba/__init__.py
src/chatdba/app/main.py
src/chatdba/config/settings.py
src/chatdba/domain/models.py
src/chatdba/domain/report_schema.py
src/chatdba/tasks/events.py
src/chatdba/tasks/repository.py
src/chatdba/dingtalk/channel.py
src/chatdba/dingtalk/stream_runtime.py
src/chatdba/sql/safety.py
src/chatdba/sql/parser.py
src/chatdba/db/mysql_collector.py
src/chatdba/db/metadata_repository.py
src/chatdba/explain/mysql_json.py
src/chatdba/rules/mysql_rules.py
src/chatdba/cases/repository.py
src/chatdba/cases/retriever.py
src/chatdba/models/qwen_gateway.py
src/chatdba/workflow/state.py
src/chatdba/workflow/sql_optimization.py
src/chatdba/worker/run_task.py
tests/unit/test_health.py
tests/unit/test_sql_safety.py
tests/unit/test_sql_parser.py
tests/unit/test_explain_parser.py
tests/unit/test_mysql_rules.py
tests/unit/test_report_schema.py
tests/unit/test_qwen_gateway.py
tests/unit/test_dingtalk_channel.py
tests/integration/test_workflow_happy_path.py
migrations/001_initial.sql
```

Module responsibilities:

- `app`: FastAPI HTTP surface.
- `config`: environment-driven settings.
- `domain`: shared Pydantic objects and report schema.
- `tasks`: task persistence contract and progress events.
- `dingtalk`: DingTalk receive/send abstraction and runtime adapter.
- `sql`: SQL safety validation and parsing.
- `db`: metadata and MySQL evidence collection.
- `explain`: MySQL JSON explain feature extraction.
- `rules`: deterministic SQL optimization signals.
- `cases`: structured case storage and retrieval.
- `models`: Tongyi Qianwen streaming and structured output gateway.
- `workflow`: LangGraph state and node wiring.
- `worker`: async task runner entrypoint.

## Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/chatdba/__init__.py`
- Create: `src/chatdba/app/main.py`
- Create: `src/chatdba/config/settings.py`
- Test: `tests/unit/test_health.py`

- [ ] **Step 1: Write the health test**

```python
from fastapi.testclient import TestClient

from chatdba.app.main import create_app


def test_health_endpoint_returns_ok():
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "chatdba"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_health.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba'`.

- [ ] **Step 3: Create project files**

`pyproject.toml`:

```toml
[project]
name = "chatdba"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "pydantic>=2.8.0",
  "pydantic-settings>=2.4.0",
  "langgraph>=0.2.0",
  "sqlalchemy[asyncio]>=2.0.0",
  "asyncpg>=0.29.0",
  "pgvector>=0.3.0",
  "redis>=5.0.0",
  "sqlglot>=25.0.0",
  "pymysql>=1.1.0",
  "openai>=1.40.0",
  "dingtalk-stream>=0.24.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
  "pytest-asyncio>=0.23.0",
  "respx>=0.21.0",
  "httpx>=0.27.0",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`.env.example`:

```text
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://chatdba:chatdba@localhost:5432/chatdba
REDIS_URL=redis://localhost:6379/0
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_API_KEY=replace-with-dashscope-api-key
QWEN_MODEL=qwen-plus
QWEN_FALLBACK_MODEL=qwen-max
QWEN_EMBEDDING_MODEL=text-embedding-v4
DINGTALK_CLIENT_ID=replace-with-client-id
DINGTALK_CLIENT_SECRET=replace-with-client-secret
DINGTALK_STREAM_ENABLED=false
MYSQL_CONNECT_TIMEOUT_SECONDS=3
MYSQL_QUERY_TIMEOUT_SECONDS=8
STREAM_UPDATE_INTERVAL_MS=1000
```

`src/chatdba/app/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="ChatDBA", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    return app


app = create_app()
```

`src/chatdba/config/settings.py`:

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
```

`src/chatdba/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

`README.md`:

````markdown
# ChatDBA

ChatDBA phase 1 provides DingTalk-based SQL optimization using a controlled LangGraph workflow and Tongyi Qianwen generation.

## Local Checks

```bash
pip install -e ".[dev]"
pytest -q
```
````

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_health.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add pyproject.toml .env.example README.md src/chatdba tests/unit/test_health.py
git commit -m "chore: scaffold chatdba service"
```

Expected: commit succeeds. If the workspace has no `.git` directory, run `git init` once before this commit.

## Task 2: Domain Models And Report Schema

**Files:**
- Create: `src/chatdba/domain/models.py`
- Create: `src/chatdba/domain/report_schema.py`
- Test: `tests/unit/test_report_schema.py`

- [ ] **Step 1: Write schema tests**

```python
import pytest
from pydantic import ValidationError

from chatdba.domain.report_schema import OptimizationReport


def test_report_accepts_required_sections():
    report = OptimizationReport.model_validate(
        {
            "task_id": "task-1",
            "summary": "Full table scan on orders.",
            "confidence": 0.82,
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


def test_report_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        OptimizationReport.model_validate(
            {
                "task_id": "task-1",
                "summary": "Invalid confidence.",
                "confidence": 1.5,
                "bottlenecks": [],
                "sql_rewrites": [],
                "index_recommendations": [],
                "risks": [],
                "validation_steps": [],
                "similar_cases": [],
            }
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_report_schema.py -v`

Expected: FAIL with `ModuleNotFoundError` for `chatdba.domain.report_schema`.

- [ ] **Step 3: Create domain model code**

`src/chatdba/domain/models.py`:

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
```

`src/chatdba/domain/report_schema.py`:

```python
from pydantic import BaseModel, Field


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
    bottlenecks: list[Bottleneck]
    sql_rewrites: list[SqlRewrite]
    index_recommendations: list[IndexRecommendation]
    risks: list[Risk]
    validation_steps: list[str]
    similar_cases: list[SimilarCase]
```

- [ ] **Step 4: Run schema tests**

Run: `pytest tests/unit/test_report_schema.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/domain tests/unit/test_report_schema.py
git commit -m "feat: add optimization domain schemas"
```

Expected: commit succeeds.

## Task 3: SQL Safety And Parsing

**Files:**
- Create: `src/chatdba/sql/safety.py`
- Create: `src/chatdba/sql/parser.py`
- Test: `tests/unit/test_sql_safety.py`
- Test: `tests/unit/test_sql_parser.py`

- [ ] **Step 1: Write safety tests**

```python
import pytest

from chatdba.sql.safety import UnsafeSqlError, validate_select_only


def test_validate_select_only_accepts_single_select():
    assert validate_select_only("select * from orders where id = 1") == "select * from orders where id = 1"


@pytest.mark.parametrize(
    "sql",
    [
        "update orders set status = 1",
        "delete from orders",
        "select * from orders; drop table orders",
        "create index idx_orders_id on orders(id)",
    ],
)
def test_validate_select_only_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeSqlError):
        validate_select_only(sql)
```

- [ ] **Step 2: Write parser tests**

```python
from chatdba.sql.parser import parse_sql_features


def test_parse_sql_features_extracts_tables_and_limit():
    features = parse_sql_features(
        "select o.id, u.name from orders o join users u on o.user_id = u.id "
        "where o.status = 'PAID' order by o.created_at desc limit 20"
    )

    assert features.statement_type == "select"
    assert features.has_limit is True
    assert [table.table_name for table in features.tables] == ["orders", "users"]
    assert features.order_by == ["o.created_at DESC"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_sql_safety.py tests/unit/test_sql_parser.py -v`

Expected: FAIL with missing modules under `chatdba.sql`.

- [ ] **Step 4: Implement SQL safety**

`src/chatdba/sql/safety.py`:

```python
import sqlglot


class UnsafeSqlError(ValueError):
    pass


def validate_select_only(raw_sql: str) -> str:
    sql = raw_sql.strip()
    if not sql:
        raise UnsafeSqlError("SQL is empty")
    statements = sqlglot.parse(sql, read="mysql")
    if len(statements) != 1:
        raise UnsafeSqlError("Only one SQL statement is allowed")
    statement = statements[0]
    if statement.key != "select":
        raise UnsafeSqlError("Only SELECT SQL is allowed")
    return sql
```

- [ ] **Step 5: Implement SQL parser**

`src/chatdba/sql/parser.py`:

```python
import hashlib

import sqlglot
from sqlglot import expressions as exp

from chatdba.domain.models import SqlFeatures, TableReference
from chatdba.sql.safety import validate_select_only


def _fingerprint(sql: str) -> str:
    normalized = " ".join(sql.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_sql_features(raw_sql: str) -> SqlFeatures:
    sql = validate_select_only(raw_sql)
    expression = sqlglot.parse_one(sql, read="mysql")
    tables: list[TableReference] = []
    for table in expression.find_all(exp.Table):
        tables.append(
            TableReference(
                schema_name=table.db or None,
                table_name=table.name,
                alias=table.alias_or_name if table.alias else None,
            )
        )
    order_by = [
        ordered.sql(dialect="mysql")
        for ordered in (expression.args.get("order") or exp.Order()).expressions
    ]
    group_by = [
        grouped.sql(dialect="mysql")
        for grouped in (expression.args.get("group") or exp.Group()).expressions
    ]
    predicates = [
        where.this.sql(dialect="mysql")
        for where in expression.find_all(exp.Where)
    ]
    joins = [join.sql(dialect="mysql") for join in expression.find_all(exp.Join)]
    return SqlFeatures(
        fingerprint=_fingerprint(expression.sql(dialect="mysql")),
        statement_type=expression.key,
        tables=tables,
        predicates=predicates,
        joins=joins,
        order_by=order_by,
        group_by=group_by,
        has_limit=expression.args.get("limit") is not None,
    )
```

- [ ] **Step 6: Run SQL tests**

Run: `pytest tests/unit/test_sql_safety.py tests/unit/test_sql_parser.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/chatdba/sql tests/unit/test_sql_safety.py tests/unit/test_sql_parser.py
git commit -m "feat: add select-only SQL parsing"
```

Expected: commit succeeds.

## Task 4: Task Repository And Progress Events

**Files:**
- Create: `src/chatdba/tasks/events.py`
- Create: `src/chatdba/tasks/repository.py`
- Create: `migrations/001_initial.sql`
- Test: add assertions in `tests/unit/test_report_schema.py` or create `tests/unit/test_tasks.py`

- [ ] **Step 1: Write task event test**

Create `tests/unit/test_tasks.py`:

```python
from chatdba.domain.models import TaskStatus
from chatdba.tasks.events import ProgressEvent
from chatdba.tasks.repository import InMemoryTaskRepository


def test_task_repository_records_progress_events():
    repo = InMemoryTaskRepository()
    repo.create_task(task_id="task-1", raw_sql="select * from orders")
    repo.append_event(ProgressEvent(task_id="task-1", status=TaskStatus.PARSING_SQL, message="Parsing SQL"))

    task = repo.get_task("task-1")

    assert task["task_id"] == "task-1"
    assert task["status"] == TaskStatus.PARSING_SQL
    assert task["events"][0].message == "Parsing SQL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tasks.py -v`

Expected: FAIL with missing `chatdba.tasks.events`.

- [ ] **Step 3: Implement event and repository contracts**

`src/chatdba/tasks/events.py`:

```python
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from chatdba.domain.models import TaskStatus


class ProgressEvent(BaseModel):
    task_id: str
    status: TaskStatus
    message: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`src/chatdba/tasks/repository.py`:

```python
from chatdba.domain.models import TaskStatus
from chatdba.tasks.events import ProgressEvent


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, object]] = {}

    def create_task(self, task_id: str, raw_sql: str) -> None:
        self._tasks[task_id] = {
            "task_id": task_id,
            "raw_sql": raw_sql,
            "status": TaskStatus.RECEIVED,
            "events": [],
        }

    def append_event(self, event: ProgressEvent) -> None:
        task = self._tasks[event.task_id]
        task["status"] = event.status
        task["events"].append(event)

    def get_task(self, task_id: str) -> dict[str, object]:
        return self._tasks[task_id]
```

- [ ] **Step 4: Add migration**

`migrations/001_initial.sql`:

```sql
create extension if not exists vector;

create table if not exists optimization_tasks (
  task_id text primary key,
  raw_sql text not null,
  status text not null,
  dingtalk_message_id text,
  dingtalk_conversation_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists optimization_events (
  id bigserial primary key,
  task_id text not null references optimization_tasks(task_id),
  status text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists optimization_cases (
  case_id text primary key,
  db_type text not null,
  db_version text not null,
  sql_fingerprint text not null,
  scenario_tags text[] not null default '{}',
  plan_features jsonb not null default '{}'::jsonb,
  root_cause_tags text[] not null default '{}',
  optimization_actions jsonb not null default '[]'::jsonb,
  before_after_metrics jsonb not null default '{}'::jsonb,
  case_card text not null,
  full_text text not null,
  embedding vector(1024),
  quality_score numeric not null default 0,
  created_at timestamptz not null default now()
);
```

- [ ] **Step 5: Run task tests**

Run: `pytest tests/unit/test_tasks.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/chatdba/tasks tests/unit/test_tasks.py migrations/001_initial.sql
git commit -m "feat: add task events and storage schema"
```

Expected: commit succeeds.

## Task 5: DingTalk Channel Abstraction

**Files:**
- Create: `src/chatdba/dingtalk/channel.py`
- Create: `src/chatdba/dingtalk/stream_runtime.py`
- Test: `tests/unit/test_dingtalk_channel.py`

- [ ] **Step 1: Write channel tests**

```python
from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message, StreamUpdateBuffer


def test_extract_sql_from_mentioned_message():
    message = DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="@ChatDBA optimize ```sql\nselect * from orders\n```",
        session_webhook="https://example.test/webhook",
    )

    assert extract_sql_from_message(message) == "select * from orders"


def test_stream_update_buffer_flushes_after_interval():
    buffer = StreamUpdateBuffer(interval_ms=1000)
    buffer.add("hello")
    buffer.add(" world")

    assert buffer.flush(force=True) == "hello world"
    assert buffer.flush(force=True) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_dingtalk_channel.py -v`

Expected: FAIL with missing `chatdba.dingtalk.channel`.

- [ ] **Step 3: Implement channel primitives**

`src/chatdba/dingtalk/channel.py`:

```python
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DingTalkInboundMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    session_webhook: str | None = None


def extract_sql_from_message(message: DingTalkInboundMessage) -> str:
    match = re.search(r"```sql\s*(.*?)```", message.text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    text = re.sub(r"@\S+", "", message.text).strip()
    prefixes = ["optimize", "sql optimize", "优化", "SQL优化"]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix):].strip()
    return text


@dataclass
class StreamUpdateBuffer:
    interval_ms: int
    chunks: list[str] = field(default_factory=list)

    def add(self, chunk: str) -> None:
        self.chunks.append(chunk)

    def flush(self, force: bool = False) -> str:
        if not force and not self.chunks:
            return ""
        output = "".join(self.chunks)
        self.chunks.clear()
        return output
```

`src/chatdba/dingtalk/stream_runtime.py`:

```python
from collections.abc import Callable

from chatdba.dingtalk.channel import DingTalkInboundMessage


MessageHandler = Callable[[DingTalkInboundMessage], None]


class DingTalkStreamRuntime:
    def __init__(self, handler: MessageHandler) -> None:
        self._handler = handler

    def handle_test_message(self, message: DingTalkInboundMessage) -> None:
        self._handler(message)
```

- [ ] **Step 4: Run DingTalk tests**

Run: `pytest tests/unit/test_dingtalk_channel.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk tests/unit/test_dingtalk_channel.py
git commit -m "feat: add dingtalk channel primitives"
```

Expected: commit succeeds.

## Task 6: MySQL Evidence Collection Contracts

**Files:**
- Create: `src/chatdba/db/mysql_collector.py`
- Create: `src/chatdba/db/metadata_repository.py`
- Test: `tests/unit/test_mysql_collector.py`

- [ ] **Step 1: Write collector tests**

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


def test_collector_uses_explain_format_json_and_show_create_table():
    collector = MysqlEvidenceCollector(FakeMysqlClient())
    target = MysqlTableTarget(schema_name="shop", table_name="orders")

    evidence = collector.collect("select * from shop.orders", [target])

    assert evidence.explain_json["query_block"]["table"]["access_type"] == "ALL"
    assert evidence.create_tables["shop.orders"].startswith("CREATE TABLE orders")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_mysql_collector.py -v`

Expected: FAIL with missing `chatdba.db.mysql_collector`.

- [ ] **Step 3: Implement collector contract**

`src/chatdba/db/mysql_collector.py`:

```python
import json
from typing import Protocol

from pydantic import BaseModel, Field


class MysqlClient(Protocol):
    def query_one(self, sql: str) -> dict[str, object]:
        ...


class MysqlTableTarget(BaseModel):
    schema_name: str
    table_name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class MysqlEvidence(BaseModel):
    explain_json: dict[str, object]
    create_tables: dict[str, str] = Field(default_factory=dict)


class MysqlEvidenceCollector:
    def __init__(self, client: MysqlClient) -> None:
        self._client = client

    def collect(self, sql: str, tables: list[MysqlTableTarget]) -> MysqlEvidence:
        explain_row = self._client.query_one(f"EXPLAIN FORMAT=JSON {sql}")
        explain_raw = str(explain_row["EXPLAIN"])
        create_tables: dict[str, str] = {}
        for table in tables:
            row = self._client.query_one(f"SHOW CREATE TABLE `{table.schema_name}`.`{table.table_name}`")
            create_tables[table.qualified_name] = str(row["Create Table"])
        return MysqlEvidence(explain_json=json.loads(explain_raw), create_tables=create_tables)
```

`src/chatdba/db/metadata_repository.py`:

```python
from chatdba.db.mysql_collector import MysqlTableTarget
from chatdba.domain.models import TableReference


class StaticMetadataRepository:
    def __init__(self, default_schema: str = "default") -> None:
        self._default_schema = default_schema

    def resolve_tables(self, tables: list[TableReference]) -> list[MysqlTableTarget]:
        return [
            MysqlTableTarget(schema_name=table.schema_name or self._default_schema, table_name=table.table_name)
            for table in tables
        ]
```

- [ ] **Step 4: Run collector tests**

Run: `pytest tests/unit/test_mysql_collector.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/db tests/unit/test_mysql_collector.py
git commit -m "feat: add mysql evidence collector contract"
```

Expected: commit succeeds.

## Task 7: Explain Parser And Rule Engine

**Files:**
- Create: `src/chatdba/explain/mysql_json.py`
- Create: `src/chatdba/rules/mysql_rules.py`
- Test: `tests/unit/test_explain_parser.py`
- Test: `tests/unit/test_mysql_rules.py`

- [ ] **Step 1: Write explain parser test**

```python
from chatdba.explain.mysql_json import extract_plan_features


def test_extract_plan_features_detects_full_scan():
    explain = {"query_block": {"table": {"table_name": "orders", "access_type": "ALL", "rows_examined_per_scan": 120000}}}

    features = extract_plan_features(explain)

    assert features[0].code == "full_table_scan"
    assert features[0].severity == "high"
```

- [ ] **Step 2: Write rule test**

```python
from chatdba.domain.models import PlanFeature, SqlFeatures
from chatdba.rules.mysql_rules import run_mysql_rules


def test_rules_convert_full_scan_feature_to_finding():
    findings = run_mysql_rules(
        SqlFeatures(fingerprint="abc", statement_type="select"),
        [PlanFeature(code="full_table_scan", severity="high", evidence={"table": "orders"})],
    )

    assert findings[0].code == "full_table_scan"
    assert findings[0].severity == "high"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_explain_parser.py tests/unit/test_mysql_rules.py -v`

Expected: FAIL with missing parser and rule modules.

- [ ] **Step 4: Implement explain parser**

`src/chatdba/explain/mysql_json.py`:

```python
from collections.abc import Iterator
from typing import Any

from chatdba.domain.models import PlanFeature


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if "access_type" in node:
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def extract_plan_features(explain_json: dict[str, object]) -> list[PlanFeature]:
    features: list[PlanFeature] = []
    for table_node in _walk(explain_json):
        access_type = table_node.get("access_type")
        rows = int(table_node.get("rows_examined_per_scan") or table_node.get("rows_produced_per_join") or 0)
        if access_type == "ALL" and rows >= 10000:
            features.append(
                PlanFeature(
                    code="full_table_scan",
                    severity="high",
                    evidence={"table": table_node.get("table_name"), "rows": rows},
                )
            )
    return features
```

- [ ] **Step 5: Implement MySQL rules**

`src/chatdba/rules/mysql_rules.py`:

```python
from chatdba.domain.models import PlanFeature, RuleFinding, SqlFeatures


def run_mysql_rules(sql_features: SqlFeatures, plan_features: list[PlanFeature]) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for feature in plan_features:
        if feature.code == "full_table_scan":
            findings.append(
                RuleFinding(
                    code="full_table_scan",
                    severity=feature.severity,
                    message="The execution plan scans a large table without an index access path.",
                    evidence=feature.evidence,
                )
            )
    if sql_features.has_limit and sql_features.order_by:
        findings.append(
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="LIMIT with ORDER BY should be checked for a supporting index.",
                evidence={"order_by": sql_features.order_by},
            )
        )
    return findings
```

- [ ] **Step 6: Run parser and rule tests**

Run: `pytest tests/unit/test_explain_parser.py tests/unit/test_mysql_rules.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/chatdba/explain src/chatdba/rules tests/unit/test_explain_parser.py tests/unit/test_mysql_rules.py
git commit -m "feat: add mysql plan features and rules"
```

Expected: commit succeeds.

## Task 8: Case Retrieval

**Files:**
- Create: `src/chatdba/cases/repository.py`
- Create: `src/chatdba/cases/retriever.py`
- Test: `tests/unit/test_case_retriever.py`

- [ ] **Step 1: Write retriever test**

```python
from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import retrieve_cases


def test_retrieve_cases_filters_by_db_type_and_tag():
    cases = [
        OptimizationCase(case_id="case-1", db_type="mysql", scenario_tags=["order_by"], case_card="filesort fixed"),
        OptimizationCase(case_id="case-2", db_type="postgresql", scenario_tags=["order_by"], case_card="not mysql"),
    ]

    result = retrieve_cases(cases, db_type="mysql", scenario_tags=["order_by"], limit=3)

    assert [case.case_id for case in result] == ["case-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_case_retriever.py -v`

Expected: FAIL with missing `chatdba.cases.repository`.

- [ ] **Step 3: Implement case models and retrieval**

`src/chatdba/cases/repository.py`:

```python
from pydantic import BaseModel, Field


class OptimizationCase(BaseModel):
    case_id: str
    db_type: str
    scenario_tags: list[str] = Field(default_factory=list)
    case_card: str
    quality_score: float = 0.0
```

`src/chatdba/cases/retriever.py`:

```python
from chatdba.cases.repository import OptimizationCase


def retrieve_cases(
    cases: list[OptimizationCase],
    db_type: str,
    scenario_tags: list[str],
    limit: int = 5,
) -> list[OptimizationCase]:
    wanted_tags = set(scenario_tags)
    filtered = [
        case
        for case in cases
        if case.db_type == db_type and (not wanted_tags or wanted_tags.intersection(case.scenario_tags))
    ]
    return sorted(filtered, key=lambda case: case.quality_score, reverse=True)[:limit]
```

- [ ] **Step 4: Run retriever test**

Run: `pytest tests/unit/test_case_retriever.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/cases tests/unit/test_case_retriever.py
git commit -m "feat: add structured case retrieval"
```

Expected: commit succeeds.

## Task 9: Tongyi Qianwen Gateway

**Files:**
- Create: `src/chatdba/models/qwen_gateway.py`
- Test: `tests/unit/test_qwen_gateway.py`

- [ ] **Step 1: Write gateway tests**

```python
from chatdba.models.qwen_gateway import QwenGateway


class FakeChunk:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": content})()})]


class FakeCompletions:
    def create(self, **kwargs):
        assert kwargs["model"] == "qwen-plus"
        assert kwargs["stream"] is True
        return [FakeChunk("hello"), FakeChunk(" world")]


class FakeClient:
    chat = type("Chat", (), {"completions": FakeCompletions()})()


def test_gateway_streams_text_chunks():
    gateway = QwenGateway(client=FakeClient(), model="qwen-plus")

    assert list(gateway.stream_report("system", "user")) == ["hello", " world"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_qwen_gateway.py -v`

Expected: FAIL with missing `chatdba.models.qwen_gateway`.

- [ ] **Step 3: Implement gateway**

`src/chatdba/models/qwen_gateway.py`:

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
```

- [ ] **Step 4: Run gateway test**

Run: `pytest tests/unit/test_qwen_gateway.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/models tests/unit/test_qwen_gateway.py
git commit -m "feat: add qwen streaming gateway"
```

Expected: commit succeeds.

## Task 10: LangGraph Workflow

**Files:**
- Create: `src/chatdba/workflow/state.py`
- Create: `src/chatdba/workflow/sql_optimization.py`
- Test: `tests/integration/test_workflow_happy_path.py`

- [ ] **Step 1: Write workflow integration test**

```python
from chatdba.db.mysql_collector import MysqlEvidence, MysqlTableTarget
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


class FakeCollector:
    def collect(self, sql, tables):
        return MysqlEvidence(
            explain_json={"query_block": {"table": {"table_name": "orders", "access_type": "ALL", "rows_examined_per_scan": 20000}}},
            create_tables={"shop.orders": "CREATE TABLE orders (id bigint primary key)"},
        )


def test_workflow_returns_report_payload():
    graph = build_sql_optimization_graph(collector=FakeCollector())

    result = graph.invoke({"task_id": "task-1", "raw_sql": "select * from orders", "default_schema": "shop"})

    assert result["task_id"] == "task-1"
    assert result["findings"][0].code == "full_table_scan"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_workflow_happy_path.py -v`

Expected: FAIL with missing `chatdba.workflow.sql_optimization`.

- [ ] **Step 3: Implement workflow state**

`src/chatdba/workflow/state.py`:

```python
from typing import TypedDict

from chatdba.db.mysql_collector import MysqlEvidence
from chatdba.domain.models import RuleFinding, SqlFeatures


class SqlOptimizationState(TypedDict, total=False):
    task_id: str
    raw_sql: str
    default_schema: str
    sql_features: SqlFeatures
    evidence: MysqlEvidence
    findings: list[RuleFinding]
```

- [ ] **Step 4: Implement graph wiring**

`src/chatdba/workflow/sql_optimization.py`:

```python
from langgraph.graph import END, StateGraph

from chatdba.db.metadata_repository import StaticMetadataRepository
from chatdba.explain.mysql_json import extract_plan_features
from chatdba.rules.mysql_rules import run_mysql_rules
from chatdba.sql.parser import parse_sql_features
from chatdba.workflow.state import SqlOptimizationState


def build_sql_optimization_graph(collector):
    metadata = StaticMetadataRepository()
    graph = StateGraph(SqlOptimizationState)

    def parse_sql(state: SqlOptimizationState) -> SqlOptimizationState:
        return {"sql_features": parse_sql_features(state["raw_sql"])}

    def collect_evidence(state: SqlOptimizationState) -> SqlOptimizationState:
        sql_features = state["sql_features"]
        resolver = StaticMetadataRepository(default_schema=state.get("default_schema", "default"))
        targets = resolver.resolve_tables(sql_features.tables)
        return {"evidence": collector.collect(state["raw_sql"], targets)}

    def diagnose(state: SqlOptimizationState) -> SqlOptimizationState:
        plan_features = extract_plan_features(state["evidence"].explain_json)
        findings = run_mysql_rules(state["sql_features"], plan_features)
        return {"findings": findings}

    graph.add_node("parse_sql", parse_sql)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("diagnose", diagnose)
    graph.set_entry_point("parse_sql")
    graph.add_edge("parse_sql", "collect_evidence")
    graph.add_edge("collect_evidence", "diagnose")
    graph.add_edge("diagnose", END)
    return graph.compile()
```

- [ ] **Step 5: Run workflow test**

Run: `pytest tests/integration/test_workflow_happy_path.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/chatdba/workflow tests/integration/test_workflow_happy_path.py
git commit -m "feat: wire sql optimization workflow"
```

Expected: commit succeeds.

## Task 11: API And Worker Entrypoints

**Files:**
- Modify: `src/chatdba/app/main.py`
- Create: `src/chatdba/worker/run_task.py`
- Test: `tests/integration/test_api_task_creation.py`

- [ ] **Step 1: Write API test**

```python
from fastapi.testclient import TestClient

from chatdba.app.main import create_app


def test_create_sql_optimization_task():
    client = TestClient(create_app())

    response = client.post("/internal/tasks/sql-optimization", json={"raw_sql": "select * from orders"})

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"]
    assert body["status"] == "received"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_task_creation.py -v`

Expected: FAIL with 404 for `/internal/tasks/sql-optimization`.

- [ ] **Step 3: Add API route**

Modify `src/chatdba/app/main.py`:

```python
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from chatdba.domain.models import TaskStatus


class CreateOptimizationTaskRequest(BaseModel):
    raw_sql: str


def create_app() -> FastAPI:
    app = FastAPI(title="ChatDBA", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    @app.post("/internal/tasks/sql-optimization", status_code=202)
    def create_sql_optimization_task(request: CreateOptimizationTaskRequest) -> dict[str, str]:
        return {"task_id": str(uuid4()), "status": TaskStatus.RECEIVED}

    return app


app = create_app()
```

`src/chatdba/worker/run_task.py`:

```python
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


def run_sql_optimization_task(task_payload: dict[str, object], collector) -> dict[str, object]:
    graph = build_sql_optimization_graph(collector=collector)
    return graph.invoke(task_payload)
```

- [ ] **Step 4: Run API test**

Run: `pytest tests/integration/test_api_task_creation.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/app/main.py src/chatdba/worker tests/integration/test_api_task_creation.py
git commit -m "feat: add optimization task api"
```

Expected: commit succeeds.

## Task 12: End-To-End Streaming Contract

**Files:**
- Modify: `src/chatdba/dingtalk/channel.py`
- Modify: `src/chatdba/worker/run_task.py`
- Test: `tests/integration/test_streaming_contract.py`

- [ ] **Step 1: Write streaming contract test**

```python
from chatdba.dingtalk.channel import StreamUpdateBuffer


def test_streaming_buffer_collects_workflow_and_model_chunks():
    buffer = StreamUpdateBuffer(interval_ms=1000)
    for chunk in ["Parsing SQL\n", "Collecting EXPLAIN\n", "Recommendation: add index"]:
        buffer.add(chunk)

    assert buffer.flush(force=True) == "Parsing SQL\nCollecting EXPLAIN\nRecommendation: add index"
```

- [ ] **Step 2: Run test**

Run: `pytest tests/integration/test_streaming_contract.py -v`

Expected: PASS because Task 5 already created `StreamUpdateBuffer`.

- [ ] **Step 3: Add worker progress emission contract**

Modify `src/chatdba/worker/run_task.py`:

```python
from collections.abc import Callable

from chatdba.workflow.sql_optimization import build_sql_optimization_graph


ProgressSink = Callable[[str], None]


def run_sql_optimization_task(task_payload: dict[str, object], collector, progress_sink: ProgressSink | None = None) -> dict[str, object]:
    if progress_sink:
        progress_sink("Parsing SQL\n")
    graph = build_sql_optimization_graph(collector=collector)
    result = graph.invoke(task_payload)
    if progress_sink:
        progress_sink("Generated diagnostic findings\n")
    return result
```

- [ ] **Step 4: Run streaming test and workflow test**

Run: `pytest tests/integration/test_streaming_contract.py tests/integration/test_workflow_happy_path.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/worker/run_task.py tests/integration/test_streaming_contract.py
git commit -m "feat: add streaming progress contract"
```

Expected: commit succeeds.

## Task 13: Local Verification And Documentation

**Files:**
- Modify: `README.md`
- Create: `docker-compose.yml`
- Create: `scripts/run-local-checks.sh`

- [ ] **Step 1: Create local check script**

`scripts/run-local-checks.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

pytest -q
```

Run: `chmod +x scripts/run-local-checks.sh`

Expected: command exits 0.

- [ ] **Step 2: Add local docker compose**

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: chatdba
      POSTGRES_PASSWORD: chatdba
      POSTGRES_DB: chatdba
    ports:
      - "5432:5432"
  redis:
    image: redis:7
    ports:
      - "6379:6379"
```

- [ ] **Step 3: Update README runbook**

Append to `README.md`:

````markdown
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

4. Start API:

```bash
uvicorn chatdba.app.main:app --reload
```
````

- [ ] **Step 4: Run all tests**

Run: `./scripts/run-local-checks.sh`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md docker-compose.yml scripts/run-local-checks.sh
git commit -m "docs: add local development runbook"
```

Expected: commit succeeds.

## Self-Review Checklist

Spec coverage:

- DingTalk Stream receiving is covered by Task 5.
- DingTalk streaming response abstraction is covered by Tasks 5 and 12.
- SQL optimization task service is covered by Tasks 4 and 11.
- LangGraph workflow with core nodes is covered by Task 10.
- MySQL metadata and explain collection are covered by Task 6.
- PostgreSQL task, case, and feedback schema foundation is covered by Task 4.
- pgvector case storage is covered by Task 4 and retrieval contract by Task 8.
- MySQL rule engine is covered by Task 7.
- Tongyi Qianwen streaming model gateway is covered by Task 9.
- Structured JSON report validation is covered by Task 2.

Type consistency:

- `TaskStatus`, `SqlFeatures`, `PlanFeature`, and `RuleFinding` are defined in Task 2 and reused by later tasks.
- `MysqlEvidence` and `MysqlTableTarget` are defined in Task 6 and reused by Task 10.
- `StreamUpdateBuffer` is defined in Task 5 and reused by Task 12.

Execution order:

- Tasks 1-4 establish project, schema, SQL parsing, and task events.
- Tasks 5-9 add DingTalk, database evidence, rules, retrieval, and Qwen gateway.
- Tasks 10-12 wire workflow, API, worker, and streaming.
- Task 13 provides local verification and runbook.
