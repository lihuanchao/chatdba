# ChatDBA 完整调用案例

这个样例覆盖一次完整 SQL 优化调用需要的三类核心输入：

- 用户在钉钉中输入的 SQL
- 源数据库上的真实表结构
- 历史优化案例

本目录还提供了一个可执行脚本：

```bash
PYTHONPATH=src python examples/complete_sql_optimization_call_case.py
```

脚本会模拟已成功采集 `EXPLAIN FORMAT=JSON` 和 `SHOW CREATE TABLE`，注入一条历史案例，并输出最终中文 Markdown 优化报告。

## 1. 钉钉输入 SQL

用户可以在钉钉机器人聊天窗口输入：

```text
SQL优化
SELECT
  o.id,
  o.order_no,
  o.amount,
  o.created_at,
  u.user_name
FROM shop.orders AS o
JOIN shop.users AS u ON u.id = o.user_id
WHERE o.tenant_id = 10001
  AND o.status = 1
ORDER BY o.created_at DESC
LIMIT 20;
```

这个 SQL 的典型问题是：订单表按 `tenant_id + status` 过滤后再按 `created_at` 排序取前 20 条，但当前只有 `user_id` 和 `status` 单列索引，容易出现大范围扫描和 `Using filesort`。

## 2. 源数据库表结构

下面是源库 `shop` 中的示例表结构。ChatDBA 真实运行时会到源库执行：

```sql
EXPLAIN FORMAT=JSON <用户SQL>;
SHOW CREATE TABLE `shop`.`orders`;
SHOW CREATE TABLE `shop`.`users`;
```

示例建表语句：

```sql
CREATE DATABASE IF NOT EXISTS `shop` DEFAULT CHARACTER SET utf8mb4;
USE `shop`;

CREATE TABLE `orders` (
  `id` bigint NOT NULL,
  `tenant_id` bigint NOT NULL,
  `user_id` bigint NOT NULL,
  `status` tinyint NOT NULL,
  `order_no` varchar(64) NOT NULL,
  `amount` decimal(12,2) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_orders_user_id` (`user_id`),
  KEY `idx_orders_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `users` (
  `id` bigint NOT NULL,
  `tenant_id` bigint NOT NULL,
  `user_name` varchar(64) NOT NULL,
  `status` tinyint NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_users_tenant_status` (`tenant_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

建议的目标优化索引通常是：

```sql
CREATE INDEX idx_orders_tenant_status_created_user
ON shop.orders (tenant_id, status, created_at, user_id);
```

## 3. 元数据库路由数据

如果你使用当前代码中的默认表名：

```text
METADATA_ROUTE_TABLE=table_routes
METADATA_INSTANCE_TABLE=db_instances
```

元数据库中至少需要有下面两张表或等价视图。

```sql
CREATE TABLE IF NOT EXISTS db_instances (
  instance_id varchar(64) PRIMARY KEY,
  host varchar(255) NOT NULL,
  port int NOT NULL DEFAULT 3306,
  readonly_username varchar(128) NOT NULL,
  readonly_password varchar(255) NOT NULL,
  default_schema varchar(128),
  db_type varchar(32) NOT NULL DEFAULT 'mysql',
  version varchar(32),
  enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS table_routes (
  schema_name varchar(128) NOT NULL,
  table_name varchar(128) NOT NULL,
  instance_id varchar(64) NOT NULL,
  PRIMARY KEY (schema_name, table_name),
  KEY idx_table_routes_instance_id (instance_id)
);
```

示例路由数据：

```sql
INSERT INTO db_instances (
  instance_id,
  host,
  port,
  readonly_username,
  readonly_password,
  default_schema,
  db_type,
  version,
  enabled
) VALUES (
  'mysql-shop-prod-01',
  '127.0.0.1',
  3306,
  'chatdba_ro',
  'replace-with-readonly-password',
  'shop',
  'mysql',
  '8.0.36',
  true
)
ON DUPLICATE KEY UPDATE
  host = VALUES(host),
  port = VALUES(port),
  readonly_username = VALUES(readonly_username),
  readonly_password = VALUES(readonly_password),
  default_schema = VALUES(default_schema),
  db_type = VALUES(db_type),
  version = VALUES(version),
  enabled = VALUES(enabled);

INSERT INTO table_routes (schema_name, table_name, instance_id) VALUES
  ('shop', 'orders', 'mysql-shop-prod-01'),
  ('shop', 'users', 'mysql-shop-prod-01')
ON DUPLICATE KEY UPDATE
  instance_id = VALUES(instance_id);
```

## 4. 历史优化案例

当前代码运行时通过 `OptimizationReportComposer(cases=[...])` 注入案例。完整案例对象如下：

```python
OptimizationCase(
    case_id="case-mysql8-order-list-filesort-001",
    db_type="mysql",
    db_version_major="8.0",
    sql_type="select",
    workload_type="oltp",
    scenario_tags=["join", "order_by", "limit"],
    plan_symptom_tags=["all", "using_filesort"],
    root_cause_tags=["missing_composite_index"],
    action_tags=["add_composite_index", "sql_rewrite"],
    estimated_rows_bucket="10m+",
    case_card=(
        "MySQL 8.0 / 订单列表 JOIN + ORDER BY + LIMIT / 执行计划出现 ALL + "
        "Using filesort / 根因：缺少匹配过滤与排序的联合索引 / 优化后 18s -> 120ms"
    ),
    full_text=(
        "订单列表按 tenant_id、status 过滤后按 created_at 倒序取前 N 条。"
        "原 SQL 只能走 status 单列索引或全表扫描，排序阶段触发 filesort。"
        "新增 orders(tenant_id, status, created_at, user_id) 后，过滤、排序与回表成本显著下降。"
    ),
    keyword_score=0.92,
    vector_score=0.86,
    rerank_score=0.94,
    quality_score=0.95,
)
```

如果你先把案例落到当前 PostgreSQL 表 `optimization_cases`，可以这样写：

```sql
INSERT INTO optimization_cases (
  case_id,
  db_type,
  db_version,
  sql_fingerprint,
  scenario_tags,
  plan_features,
  root_cause_tags,
  optimization_actions,
  before_after_metrics,
  case_card,
  full_text,
  quality_score
) VALUES (
  'case-mysql8-order-list-filesort-001',
  'mysql',
  '8.0',
  'order-list-join-order-by-limit-demo',
  ARRAY['join', 'order_by', 'limit'],
  '{"plan_symptom_tags":["all","using_filesort"],"estimated_rows_bucket":"10m+"}'::jsonb,
  ARRAY['missing_composite_index'],
  '[{"type":"add_index","ddl":"CREATE INDEX idx_orders_tenant_status_created_user ON shop.orders (tenant_id, status, created_at, user_id);"},{"type":"sql_rewrite","description":"只返回必要列，确保 WHERE 与 ORDER BY 能复用联合索引"}]'::jsonb,
  '{"before_latency_ms":18000,"after_latency_ms":120,"before_rows_examined":12500000,"after_rows_examined":20}'::jsonb,
  'MySQL 8.0 / 订单列表 JOIN + ORDER BY + LIMIT / ALL + Using filesort / 缺少联合索引 / 18s -> 120ms',
  '订单列表按 tenant_id、status 过滤后按 created_at 倒序取前 N 条。原 SQL 触发大范围扫描和 filesort。新增 orders(tenant_id, status, created_at, user_id) 后显著降低扫描与排序成本。',
  0.95
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;
```

注意：当前运行时代码已经会在服务启动时自动加载 `optimization_cases`。如果你希望快速导入一批可用于混合检索验证的案例，可以直接执行 [seed_optimization_cases.sql](/data/chatdba/examples/seed_optimization_cases.sql)。

## 5. 本地调用

执行：

```bash
PYTHONPATH=src python examples/complete_sql_optimization_call_case.py
```

你应该能看到类似下面的输出：

````markdown
# SQL优化报告

## 任务信息
- 任务ID：`demo-order-list-filesort`
- 证据级别：`full`
- 置信度：`high` (0.90)

## 结论摘要
执行计划显示大表全表扫描，缺少可用索引访问路径。

## SQL重写建议
...

## 索引推荐
```sql
CREATE INDEX idx_orders_tenant_id_status_created_at_user_id
ON orders(tenant_id, status, created_at, user_id);
```

## 相似案例
- `case-mysql8-order-list-filesort-001`：MySQL 8.0 / 订单列表 JOIN + ORDER BY + LIMIT / 执行计划出现 ALL + Using filesort / 根因：缺少匹配过滤与排序的联合索引 / 优化后 18s -> 120ms
````
