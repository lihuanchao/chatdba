# ChatDBA

ChatDBA 是面向数据库运维场景的智能助手，当前主要提供以下能力：

- SQL 优化：识别用户输入的 `select` SQL，结合表结构、执行计划、元数据路由和历史案例生成优化报告。
- 智能诊断：根据数据库告警信息提取告警时间、管理 IP、系统信息，获取 TopSQL 和 Prometheus 监控指标后生成故障诊断报告。
- 钉钉接入：支持钉钉 Stream 机器人对话，SQL 优化和故障诊断共用同一会话入口。
- 告警自动触发：支持监听告警 MySQL 表 binlog，新告警命中规则后自动触发智能诊断，并通过钉钉群机器人 webhook 推送报告。
- Docker 部署：提供生产 compose，使用同一个镜像启动 API、钉钉 Stream worker、告警 binlog worker。

## 目录结构

```text
src/chatdba/app                 FastAPI 接口和 SSE 流式输出
src/chatdba/dingtalk            钉钉 Stream 接入、消息识别、回复发送
src/chatdba/workflow            SQL 优化和智能诊断工作流
src/chatdba/db                  元数据路由、MySQL 证据采集
src/chatdba/fault               TopSQL、Prometheus、CMDB 相关采集逻辑
src/chatdba/alarm_binlog        告警 binlog 监听和 webhook 推送
src/chatdba/prompts             SQL 优化画像和报告提示词
migrations                      应用 PostgreSQL 元数据表迁移
examples                        示例数据和调用样例
scripts                         本地检查和案例向量回填脚本
```

## 运行环境

- Python 3.11+
- PostgreSQL 16，建议安装 pgvector 扩展
- Redis 7
- MySQL 元数据库，用于 `table_routes`、`db_instances`、`cmd_hosts`
- 目标业务 MySQL，用于 SQL 优化采集 `SHOW CREATE TABLE` 和 `EXPLAIN FORMAT=JSON`
- 统一慢日志 MySQL，用于智能诊断采集 TopSQL
- Prometheus MCP Server，HTTP API 作为兜底可选
- 钉钉 Stream 机器人和钉钉群机器人 webhook

## 快速开始

启动本地依赖：

```bash
docker compose up -d
```

安装依赖：

```bash
source chatdba-venv/bin/activate
pip install -e ".[dev]"
```

准备配置：

```bash
cp .env.example .env
```

初始化应用 PostgreSQL 表：

```bash
psql "$POSTGRES_MIGRATION_URL" -f migrations/001_initial.sql
psql "$POSTGRES_MIGRATION_URL" -f migrations/002_agent_token_usage.sql
```

启动 API：

```bash
uvicorn chatdba.app.main:app --reload
```

启动钉钉 Stream worker：

```bash
chatdba-dingtalk
```

启动告警 binlog worker：

```bash
chatdba-alarm-binlog
```

## API

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

流式入口：

```bash
curl -N -X POST http://127.0.0.1:8000/v1/stream \
  -H 'Content-Type: application/json' \
  -d '{"input":"SQL优化\nselect * from orders where user_id = 100"}'
```

`/v1/stream` 会根据输入内容自动分流：

- 输入内容识别为 SQL 优化时，进入 SQL 优化流程。
- 输入内容以 `故障诊断`、`故障分析`、`数据库诊断`、`诊断` 开头时，进入智能诊断流程。
- 非 SQL 优化输入默认进入智能诊断流程。

## SQL 优化

SQL 优化输入按 `select` 开头识别。用户可以直接输入 SQL，也可以在 SQL 前加数据库名：

```text
select * from orders where user_id = 100;
```

```text
shop_db select * from orders where user_id = 100;
```

处理流程：

- 解析 SQL 中的表名、库名和别名。
- 如果用户显式输入数据库名，直接用数据库名匹配实例信息。
- 如果未输入数据库名，先通过 `table_routes` 匹配表所在数据库和实例。
- 如果表名在多个实例或数据库中重复，停止后续分析，并提示用户补充数据库库名。
- 如果一个多表 SQL 中任意表不能确定唯一数据库，也会提示用户补充数据库库名。
- 成功定位实例后，采集表结构、索引、`EXPLAIN FORMAT=JSON` 和关联历史案例。
- 最终报告会综合 SQL、表结构、执行计划、命中规则和关联案例生成结论摘要、SQL 重写建议和索引推荐。

证据等级：

- `full`：表结构和执行计划均采集成功。
- `partial`：部分表结构或执行计划采集失败。
- `sql_only`：无法定位实例或无法采集证据，仅基于 SQL 文本分析。

元数据配置：

```env
METADATA_MYSQL_HOST=
METADATA_MYSQL_PORT=3306
METADATA_MYSQL_USER=
METADATA_MYSQL_PASSWORD=
METADATA_MYSQL_DATABASE=
METADATA_ROUTE_TABLE=table_routes
METADATA_INSTANCE_TABLE=db_instances
```

## 历史案例召回

SQL 优化支持结构化规则召回和 pgvector 向量召回。向量召回依赖 Qwen embedding 和 `optimization_cases.embedding` 数据。

回填历史案例向量：

```bash
python scripts/backfill_case_embeddings.py --limit 100
```

导入示例案例：

```bash
psql "$POSTGRES_MIGRATION_URL" -f examples/seed_optimization_cases.sql
```

相关配置：

```env
QWEN_API_KEY=
QWEN_EMBEDDING_MODEL=text-embedding-v4
CASE_RETRIEVAL_VECTOR_TOP_K=12
CASE_RETRIEVAL_CANDIDATE_LIMIT=12
```

SQL 优化 token 用量会按业务阶段记录到 `agent_token_usage.operation`：

- `sql_problem_profile`：SQL 问题画像生成。
- `case_embedding_retrieval`：历史案例向量召回 embedding。
- `sql_optimization_report`：最终 SQL 优化报告生成。

## 智能诊断

智能诊断输入示例：

```text
故障诊断
【系统:ZJ_生产数据库维护】2026-05-13 09:45:03 实例：10.187.0.54|mysql_server_8801，ip：10.187.0.54，指标名称：<数据库主进程是否存在> 发生异常，当前指标值：'0'，请及时关注【同心云】；
```

当前处理逻辑：

- 从告警文本中提取告警时间和管理 IP。
- 告警时间按东八区 `Asia/Shanghai` 解析。
- 监控查询时间窗口为告警时间前 30 分钟到告警时间。
- Prometheus 查询前会把东八区时间转换为 UTC 时间。
- 使用管理 IP 到 CMDB 表查询业务 IP 和系统名称。
- CPU 使用率使用业务 IP 查询。
- 活跃线程数使用管理 IP 查询。
- TopSQL 连接统一慢日志库，使用告警管理 IP 作为 `db_resource.host` 过滤条件。
- 某个监控指标获取不到或 TopSQL 获取失败时，会在诊断报告中体现。
- 当前慢 SQL 数指标配置项保留，但采集链路暂未启用。
- 故障诊断任务会复用 SQL 优化的任务事件表和 `agent_token_usage` 表记录执行进度与 Qwen token 用量。
- 故障诊断 token 用量会按 `fault_profile`、`fault_adjudication`、`fault_report` 三个业务阶段记录。

TopSQL 查询窗口：

```text
ts_min = 告警时间 - 30 分钟
ts_max = 告警时间
```

TopSQL 来源 SQL：

```sql
SELECT
a.sample `SQL语句`,
    `b`.`db_max` AS `数据库名`,
    sum(b.ts_cnt) `执行次数`,
    sum(b.Query_time_sum) / sum(b.ts_cnt) `平均执行时间(秒)`,
    sum(`b`.`Query_time_sum`) `总执行时间(秒)`
FROM
    monitor_mysql_slow_query_review_rt a
    left JOIN `monitor_mysql_slow_query_review_history_rt` b ON `a`.`checksum` = `b`.`checksum`
    left JOIN `db_resource` c ON `b`.`resid_max` = `c`.`res_id`
WHERE
    `c`.`host` IS NOT NULL
    AND `c`.`port` IS NOT NULL
    AND c.is_delete != 1
    AND ((b.ts_min >= %s AND b.ts_max <= %s))
    AND `a`.`sample` != 'commit'
    AND (`b`.`db_max` != 'information_schema' OR `b`.`db_max` IS NULL)
    AND `b`.`user_max` IS NOT NULL
    AND c.user_id = 100011
    AND c.host = %s
GROUP BY
    `a`.`checksum`
ORDER BY
    sum(`b`.`Query_time_sum`) DESC
LIMIT %s;
```

TopSQL 配置：

```env
FAULT_TOP_SQL_HOST=10.186.0.27
FAULT_TOP_SQL_USER=
FAULT_TOP_SQL_PASSWORD=
FAULT_TOP_SQL_PORT=8934
FAULT_TOP_SQL_DATABASE=performance_schema
FAULT_TOP_SQL_MIN_RUNNING_SECONDS=10
FAULT_TOP_SQL_LIMIT=10
```

`FAULT_TOP_SQL_DATABASE` 需要填写统一慢日志库中包含 `monitor_mysql_slow_query_review_rt`、`monitor_mysql_slow_query_review_history_rt` 和 `db_resource` 的数据库名。

## 监控指标

监控指标获取策略为 MCP 优先、HTTP 兜底：

```text
Prometheus MCP SSE -> Prometheus HTTP query_range
```

MCP 配置：

```env
FAULT_PROMETHEUS_MCP_SSE_URL=http://10.186.42.51:8080/sse
FAULT_PROMETHEUS_MCP_HEADERS_JSON={}
FAULT_PROMETHEUS_MCP_TIMEOUT_SECONDS=50
FAULT_PROMETHEUS_MCP_SSE_READ_TIMEOUT_SECONDS=50
```

HTTP 兜底配置：

```env
FAULT_PROMETHEUS_BASE_URL=
FAULT_PROMETHEUS_TIMEOUT_SECONDS=8
FAULT_METRIC_STEP_SECONDS=300
```

活跃线程数 PromQL 模板：

```env
FAULT_ACTIVE_THREADS_QUERY_TEMPLATE=ctg_paas_30202624250003{sysCode="database_prod",tenant_id="100011",ip="{management_ip}"}
```

CPU 使用率 PromQL 由代码按业务 IP 生成。当前采集的指标包括：

- `cpu_usage`：业务 IP CPU 使用率。
- `active_threads`：管理 IP 活跃线程数。

测试 MCP 是否可用：

```bash
CHATDBA_RUN_PROMETHEUS_MCP_INTEGRATION=1 \
python -m pytest -q tests/integration/test_prometheus_mcp_server.py -s
```

可选测试参数：

```env
CHATDBA_PROMETHEUS_MCP_SSE_URL=http://10.186.42.51:8080/sse
CHATDBA_PROMETHEUS_MCP_TEST_QUERY=up
CHATDBA_PROMETHEUS_MCP_TEST_START=2026-04-30T06:00:00Z
CHATDBA_PROMETHEUS_MCP_TEST_END=2026-04-30T06:05:00Z
CHATDBA_PROMETHEUS_MCP_TEST_STEP=60s
```

## CMDB 映射

智能诊断中的告警 IP 是管理 IP，查询 CPU 指标需要转换为业务 IP。当前运行时通过元数据 MySQL 查询 CMDB 表。

CMDB 配置：

```env
FAULT_CMDB_TABLE=cmd_hosts
```

CMDB 表需要建在 `METADATA_MYSQL_DATABASE` 指向的 MySQL 库中。推荐执行 MySQL 版迁移脚本：

```bash
mysql -h "$METADATA_MYSQL_HOST" -P "$METADATA_MYSQL_PORT" \
  -u "$METADATA_MYSQL_USER" -p"$METADATA_MYSQL_PASSWORD" \
  "$METADATA_MYSQL_DATABASE" < migrations/mysql/001_fault_cmdb_hosts.sql
```

脚本内容等价于：

```sql
CREATE TABLE IF NOT EXISTS cmd_hosts (
  management_ip varchar(64) PRIMARY KEY,
  business_ip varchar(64) NOT NULL,
  system_name varchar(255) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_cmd_hosts_system_name (system_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

示例数据：

```sql
INSERT INTO cmd_hosts (management_ip, business_ip, system_name)
VALUES ('10.187.0.54', '10.186.17.54', 'ZJ_生产数据库维护');
```

说明：`migrations/003_fault_cmdb_hosts.sql` 是 PostgreSQL 建表示例；当前智能诊断运行时读取的是元数据 MySQL。

## 钉钉 Stream 机器人

启动命令：

```bash
chatdba-dingtalk
```

配置：

```env
DINGTALK_STREAM_ENABLED=true
DINGTALK_CLIENT_ID=
DINGTALK_CLIENT_SECRET=
DINGTALK_AI_CARD_TEMPLATE_ID=
DINGTALK_AI_CARD_CONTENT_FIELD=content
STREAM_UPDATE_INTERVAL_MS=1000
```

消息识别：

- SQL 优化：输入中的 SQL 以 `select` 开头。
- 智能诊断：输入以 `故障诊断`、`故障分析`、`数据库诊断`、`诊断` 开头。
- 默认兜底：无法识别为 SQL 优化时，按智能诊断处理。

模板覆盖支持在消息中指定：

```text
模板ID: your-template-id
SQL优化
select * from orders where user_id = 100;
```

或：

```text
template_id=your-template-id
SQL优化 select * from orders where user_id = 100;
```

## 告警 binlog 自动诊断

`chatdba-alarm-binlog` 会监听告警 MySQL 表的新增记录，命中过滤条件后触发智能诊断，并把 Markdown 报告推送到固定钉钉群机器人。

启动命令：

```bash
chatdba-alarm-binlog
```

配置：

```env
ALARM_MYSQL_HOST=
ALARM_MYSQL_PORT=3306
ALARM_MYSQL_USER=
ALARM_MYSQL_PASSWORD=
ALARM_MYSQL_DATABASE=syalarm_new
ALARM_MYSQL_TABLE=aps_alarm_record
ALARM_MYSQL_SERVER_ID=5011
ALARM_FILTER_SYS_CODE=database_prod
ALARM_FILTER_EVENT_CODES=1222,1654
ALARM_CHECKPOINT_FILE=/data/chatdba/alarm-checkpoint.json
ALARM_DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
ALARM_DINGTALK_TIMEOUT_SECONDS=10
ALARM_RETRY_MAX_ATTEMPTS=3
ALARM_RETRY_INITIAL_DELAY_SECONDS=1
ALARM_RETRY_MAX_DELAY_SECONDS=30
ALARM_LOG_LEVEL=INFO
```

运行行为：

- 启动时读取告警表当前 `MAX(main_record_id)`，默认只处理 worker 启动后的新增告警。
- 监听 `WriteRowsEvent`，提取 `main_record_id`、`alarm_content`、`sys_code`、`event_code`。
- 只处理 `ALARM_FILTER_SYS_CODE` 和 `ALARM_FILTER_EVENT_CODES` 命中的告警。
- 诊断成功并推送 webhook 成功后才保存 checkpoint。
- worker 重启后会从 checkpoint 继续处理，避免重复推送已成功处理的告警。

MySQL 要求：

- 告警库开启 binlog。
- binlog 建议使用 row 格式。
- `ALARM_MYSQL_USER` 需要表读取权限、`REPLICATION SLAVE`、`REPLICATION CLIENT` 权限。
- `ALARM_MYSQL_SERVER_ID` 需要在同一个复制拓扑中唯一。

## 生产 Docker 部署

生产部署使用一个镜像启动三个服务：

- `api`：FastAPI 服务。
- `dingtalk`：钉钉 Stream worker。
- `alarm-binlog`：告警 binlog worker。

准备配置：

```bash
cp .env.example .env.prod
mkdir -p data
```

编辑 `.env.prod`，至少配置：

```env
APP_ENV=prod
DATABASE_URL=postgresql+asyncpg://user:password@postgres-host:5432/chatdba
POSTGRES_MIGRATION_URL=postgresql://user:password@postgres-host:5432/chatdba
REDIS_URL=redis://redis-host:6379/0
QWEN_API_KEY=
DINGTALK_STREAM_ENABLED=true
DINGTALK_CLIENT_ID=
DINGTALK_CLIENT_SECRET=
METADATA_MYSQL_HOST=
METADATA_MYSQL_USER=
METADATA_MYSQL_PASSWORD=
METADATA_MYSQL_DATABASE=
FAULT_TOP_SQL_HOST=10.186.0.27
FAULT_TOP_SQL_USER=
FAULT_TOP_SQL_PASSWORD=
FAULT_TOP_SQL_PORT=8934
FAULT_TOP_SQL_DATABASE=
FAULT_PROMETHEUS_MCP_SSE_URL=http://prometheus-mcp-host:8080/sse
ALARM_MYSQL_HOST=
ALARM_MYSQL_USER=
ALARM_MYSQL_PASSWORD=
ALARM_CHECKPOINT_FILE=/data/chatdba/alarm-checkpoint.json
ALARM_DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

构建和启动：

```bash
docker compose -f docker-compose.prod.yml build api
docker compose -f docker-compose.prod.yml up -d
```

初始化或升级 PostgreSQL 表：

```bash
docker compose -f docker-compose.prod.yml run --rm api \
  sh -c 'psql "$POSTGRES_MIGRATION_URL" -f migrations/001_initial.sql && psql "$POSTGRES_MIGRATION_URL" -f migrations/002_agent_token_usage.sql'
```

查看日志：

```bash
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f dingtalk
docker compose -f docker-compose.prod.yml logs -f alarm-binlog
```

停止服务：

```bash
docker compose -f docker-compose.prod.yml down
```

`.env.prod` 包含生产密钥，已被 `.gitignore` 忽略，不要提交到 Git。

## 本地测试

运行全部测试：

```bash
source chatdba-venv/bin/activate
pytest -q
```

运行本地检查脚本：

```bash
PYTHON_BIN=chatdba-venv/bin/python ./scripts/run-local-checks.sh
```

告警 binlog 相关单测：

```bash
source chatdba-venv/bin/activate
python -m pytest -q tests/unit/alarm_binlog
```

编译检查：

```bash
source chatdba-venv/bin/activate
python -m compileall -q src
```

## 常见问题

`Missing required setting: ALARM_MYSQL_HOST`

检查 `.env` 或 `.env.prod` 是否配置了 `ALARM_MYSQL_HOST`。本地直接运行会读取当前目录 `.env`，Docker 生产部署会读取 `docker-compose.prod.yml` 中声明的 `.env.prod`。

`DINGTALK_STREAM_ENABLED must be true`

启动 `chatdba-dingtalk` 前必须设置：

```env
DINGTALK_STREAM_ENABLED=true
DINGTALK_CLIENT_ID=
DINGTALK_CLIENT_SECRET=
```

SQL 输出为 `sql_only`

通常是表名无法唯一匹配实例、元数据路由缺失、目标 MySQL 连接失败、`SHOW CREATE TABLE` 或 `EXPLAIN FORMAT=JSON` 采集失败。先检查 `table_routes`、`db_instances` 和目标实例连通性。

智能诊断缺少监控指标

先确认 `cmd_hosts` 中存在管理 IP 到业务 IP 的映射，再检查 Prometheus MCP 地址和 HTTP 兜底地址。报告中会列出缺失的指标或 TopSQL 获取失败原因。

Docker 构建无法拉取 `python:3.11-slim`

这是 Docker registry 或 DNS 问题，不是项目代码问题。修复镜像源或 DNS 后重新执行：

```bash
docker compose -f docker-compose.prod.yml build api
```
