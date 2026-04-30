BEGIN;

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
  'order-list-join-order-by-limit',
  ARRAY['join', 'order_by', 'limit'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["all", "using_filesort"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "2",
    "keyword_score": 0.92,
    "vector_score": 0.86,
    "rerank_score": 0.94
  }'::jsonb,
  ARRAY['missing_composite_index'],
  '[
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_tenant_status_created_user ON shop.orders (tenant_id, status, created_at, user_id);"
    },
    {
      "type": "sql_rewrite",
      "description": "只返回必要列，确保 WHERE 与 ORDER BY 共享联合索引前缀。"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 18000,
    "after_latency_ms": 120,
    "before_rows_examined": 12500000,
    "after_rows_examined": 20
  }'::jsonb,
  'MySQL 8.0 / 订单列表 JOIN + ORDER BY + LIMIT / ALL + Using filesort / 根因：缺少联合索引 / 18s -> 120ms',
  '订单列表按 tenant_id、status 过滤后按 created_at 倒序分页，执行计划出现 ALL + Using filesort。新增 orders(tenant_id, status, created_at, user_id) 后，过滤、排序与回表成本显著下降。',
  0.95
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
  'case-mysql8-deep-pagination-001',
  'mysql',
  '8.0',
  'orders-deep-pagination-order-by-limit-offset',
  ARRAY['order_by', 'limit'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["using_filesort", "range"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "1",
    "keyword_score": 0.88,
    "vector_score": 0.84,
    "rerank_score": 0.91
  }'::jsonb,
  ARRAY['deep_pagination', 'high_back_to_table_cost'],
  '[
    {
      "type": "sql_rewrite",
      "description": "改为基于最后一条记录的键集翻页，避免大 OFFSET。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_tenant_created_id ON shop.orders (tenant_id, created_at, id);"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 9200,
    "after_latency_ms": 75,
    "before_rows_examined": 860000,
    "after_rows_examined": 30
  }'::jsonb,
  'MySQL 8.0 / 深分页 ORDER BY + LIMIT / Using filesort + 回表成本高 / 根因：深分页 / 9.2s -> 75ms',
  '订单流水按 created_at 倒序翻页，原 SQL 使用 LIMIT 100000,20，导致扫描和回表成本极高。优化方案是键集翻页，并新增 tenant_id + created_at + id 联合索引。',
  0.93
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
  'case-mysql8-join-driving-table-001',
  'mysql',
  '8.0',
  'user-orders-join-wrong-driving-table',
  ARRAY['join', 'order_by', 'limit'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["all", "nested_loop"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "2",
    "keyword_score": 0.85,
    "vector_score": 0.82,
    "rerank_score": 0.90
  }'::jsonb,
  ARRAY['wrong_driving_table', 'missing_join_index'],
  '[
    {
      "type": "sql_rewrite",
      "description": "先过滤高选择性订单表，再回表关联用户信息。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_tenant_status_user_created ON shop.orders (tenant_id, status, user_id, created_at);"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 12500,
    "after_latency_ms": 260,
    "before_rows_examined": 9800000,
    "after_rows_examined": 600
  }'::jsonb,
  'MySQL 8.0 / 大表 JOIN / 驱动表错误 + 缺少关联索引 / 12.5s -> 260ms',
  '用户和订单大表关联时，优化器从低选择性的 users 表开始驱动，导致 orders 表被多次回表扫描。通过先过滤订单主表并补齐关联索引，JOIN 代价明显下降。',
  0.90
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
  'case-mysql8-group-by-temp-001',
  'mysql',
  '8.0',
  'trade-summary-group-by-temporary',
  ARRAY['group_by', 'order_by'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["using_temporary", "using_filesort"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "1",
    "keyword_score": 0.87,
    "vector_score": 0.83,
    "rerank_score": 0.89
  }'::jsonb,
  ARRAY['group_by_not_indexed'],
  '[
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_trade_detail_tenant_day_status ON rpt.trade_detail (tenant_id, trade_day, status);"
    },
    {
      "type": "sql_rewrite",
      "description": "先按过滤条件缩小结果集，再按聚合维度分组。"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 6400,
    "after_latency_ms": 410,
    "before_rows_examined": 4200000,
    "after_rows_examined": 180000
  }'::jsonb,
  'MySQL 8.0 / GROUP BY 报表 / Using temporary + Using filesort / 根因：分组列未索引化 / 6.4s -> 410ms',
  '交易汇总报表按 tenant_id、trade_day、status 过滤，再按渠道和日期聚合。原计划出现临时表和 filesort，通过补齐过滤列联合索引并调整聚合顺序后，性能明显改善。',
  0.88
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
  'case-mysql8-implicit-cast-001',
  'mysql',
  '8.0',
  'member-phone-implicit-cast',
  ARRAY['where_filter', 'equality_predicate'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["all"],
    "estimated_rows_bucket": "100k-1m",
    "tables_count_bucket": "1",
    "keyword_score": 0.84,
    "vector_score": 0.79,
    "rerank_score": 0.87
  }'::jsonb,
  ARRAY['implicit_cast', 'index_invalidated_by_function'],
  '[
    {
      "type": "sql_rewrite",
      "description": "将手机号参数按字符传入，避免对索引列做函数或隐式类型转换。"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 3100,
    "after_latency_ms": 25,
    "before_rows_examined": 730000,
    "after_rows_examined": 1
  }'::jsonb,
  'MySQL 8.0 / 条件列隐式转换 / 全表扫 / 根因：implicit cast / 3.1s -> 25ms',
  '会员手机号列为 varchar，但业务侧按 bigint 传参，导致索引失效并走全表扫描。修正参数类型后，唯一索引重新生效。',
  0.86
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
  'case-mysql57-index-merge-001',
  'mysql',
  '5.7',
  'crm-clue-list-index-merge',
  ARRAY['order_by', 'limit'],
  '{
    "sql_type": "select",
    "plan_symptom_tags": ["index_merge", "using_filesort"],
    "estimated_rows_bucket": "100k-1m",
    "tables_count_bucket": "1",
    "keyword_score": 0.82,
    "vector_score": 0.78,
    "rerank_score": 0.85
  }'::jsonb,
  ARRAY['missing_composite_index'],
  '[
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_clue_tenant_status_follow_created ON crm.clue (tenant_id, follow_status, created_at);"
    }
  ]'::jsonb,
  '{
    "before_latency_ms": 4700,
    "after_latency_ms": 140,
    "before_rows_examined": 540000,
    "after_rows_examined": 50
  }'::jsonb,
  'MySQL 5.7 / 线索列表 / index_merge + Using filesort / 根因：缺少复合索引 / 4.7s -> 140ms',
  'CRM 线索列表在 MySQL 5.7 上同时按 tenant_id、follow_status 过滤并按 created_at 倒序展示。优化器采用 index_merge 后仍需 filesort，补齐复合索引后性能稳定下降。',
  0.83
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

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
) VALUES
(
  'case-mysql8-not-in-nullable-subquery-001',
  'mysql',
  '8.0',
  'customer-no-orders-not-in-nullable',
  ARRAY['not_in_subquery', 'subquery', 'null_check'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["dependent_subquery"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.88,
    "vector_score": 0.82,
    "rerank_score": 0.90
  }$json$::jsonb,
  ARRAY['null_sensitive_not_in'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer where c_custkey not in (select o_custkey from orders)",
      "after_sql": "select * from customer where c_custkey not in (select o_custkey from orders where o_custkey is not null)",
      "description": "NOT IN 子查询的选择列可能为 NULL 时，先在子查询中过滤 NULL，避免结果集异常为空。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 4200,
    "after_latency_ms": 980,
    "before_rows_examined": 1800000,
    "after_rows_examined": 420000
  }$json$::jsonb,
  'MySQL 8.0 / NOT IN 可空子查询 / 结果语义风险 + dependent subquery / 根因：null-sensitive NOT IN',
  $text$查询无订单客户时使用 NOT IN 子查询，orders.o_custkey 允许 NULL，可能导致外层结果全部为空。优化是在子查询加 IS NOT NULL 条件；如果可接受半连接语义，也可评估改写为 NOT EXISTS。$text$,
  0.91
),
(
  'case-mysql8-count-subquery-to-exists-001',
  'mysql',
  '8.0',
  'customer-has-orders-count-subquery',
  ARRAY['subquery', 'aggregate', 'exists_subquery'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["dependent_subquery"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.89,
    "vector_score": 0.84,
    "rerank_score": 0.91
  }$json$::jsonb,
  ARRAY['count_subquery_to_exists'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer c where (select count(*) from orders o where o.o_custkey = c.c_custkey) > 0",
      "after_sql": "select * from customer c where exists (select 1 from orders o where o.o_custkey = c.c_custkey)",
      "description": "将 COUNT 标量子查询改为 EXISTS，找到第一条匹配记录即可停止，减少聚合与扫描成本。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_custkey ON tpch.orders (o_custkey);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 8600,
    "after_latency_ms": 520,
    "before_rows_examined": 6200000,
    "after_rows_examined": 160000
  }$json$::jsonb,
  'MySQL 8.0 / COUNT(*) > 0 标量子查询 / dependent subquery / 根因：存在性判断使用聚合',
  $text$业务只需要判断客户是否存在订单，但 SQL 使用 COUNT(*) > 0 标量子查询，导致每个外层客户都执行聚合。改写为 EXISTS 后，优化器可走半连接或索引探测。$text$,
  0.93
),
(
  'case-mysql57-group-by-order-null-001',
  'mysql',
  '5.7',
  'lineitem-group-by-implicit-sort',
  ARRAY['group_by'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["using_filesort"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "1",
    "keyword_score": 0.86,
    "vector_score": 0.80,
    "rerank_score": 0.87
  }$json$::jsonb,
  ARRAY['group_by_implicit_sort'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select l_orderkey, sum(l_quantity) from lineitem group by l_orderkey",
      "after_sql": "select l_orderkey, sum(l_quantity) from lineitem group by l_orderkey order by null",
      "description": "MySQL 5.7 GROUP BY 默认排序时，可追加 ORDER BY NULL 取消不必要排序。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 7300,
    "after_latency_ms": 4100,
    "before_rows_examined": 18000000,
    "after_rows_examined": 18000000
  }$json$::jsonb,
  'MySQL 5.7 / GROUP BY 隐式排序 / Using filesort / 根因：不需要结果排序',
  $text$MySQL 5.7 中 GROUP BY 可能触发隐式排序，报表只需要分组结果不要求排序。追加 ORDER BY NULL 后可避免 filesort，降低 CPU 和临时空间压力。$text$,
  0.86
),
(
  'case-mysql8-function-on-index-column-001',
  'mysql',
  '8.0',
  'orders-function-on-date-index',
  ARRAY['where_filter', 'function_predicate', 'equality_predicate'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["all", "index_not_used"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "1",
    "keyword_score": 0.91,
    "vector_score": 0.87,
    "rerank_score": 0.93
  }$json$::jsonb,
  ARRAY['index_invalidated_by_function'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from orders where adddate(o_orderdate, interval 31 day) = date '2019-10-10'",
      "after_sql": "select * from orders where o_orderdate = subdate(date '2019-10-10', interval 31 day)",
      "description": "将索引列上的函数计算移动到常量侧，使 o_orderdate 可以使用索引。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_orderdate ON tpch.orders (o_orderdate);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 11200,
    "after_latency_ms": 35,
    "before_rows_examined": 15000000,
    "after_rows_examined": 240
  }$json$::jsonb,
  'MySQL 8.0 / 索引列函数计算 / ALL + index_not_used / 根因：函数包裹索引列',
  $text$o_orderdate 上有索引，但 WHERE 中对 o_orderdate 使用 adddate 函数，导致索引不可用。将计算改到常量端后，等值条件重新具备可搜索性。$text$,
  0.94
),
(
  'case-mysql8-having-pushdown-001',
  'mysql',
  '8.0',
  'customer-group-having-pushdown',
  ARRAY['group_by', 'having', 'range_predicate'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["using_temporary"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "1",
    "keyword_score": 0.84,
    "vector_score": 0.80,
    "rerank_score": 0.86
  }$json$::jsonb,
  ARRAY['having_not_pushed_down'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select c_custkey, count(*) from customer group by c_custkey having c_custkey < 100",
      "after_sql": "select c_custkey, count(*) from customer where c_custkey < 100 group by c_custkey",
      "description": "HAVING 条件不含聚合函数时，下推到 WHERE 提前过滤。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 2800,
    "after_latency_ms": 90,
    "before_rows_examined": 1500000,
    "after_rows_examined": 100
  }$json$::jsonb,
  'MySQL 8.0 / HAVING 非聚合条件 / Using temporary / 根因：过滤未下推',
  $text$HAVING c_custkey < 100 不依赖聚合结果，可以提前放到 WHERE 中使用主键范围访问，显著减少分组输入行数。$text$,
  0.88
),
(
  'case-mysql8-in-subquery-to-join-001',
  'mysql',
  '8.0',
  'orders-in-subquery-to-join',
  ARRAY['in_subquery', 'subquery', 'join', 'where_filter'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["dependent_subquery", "all"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.87,
    "vector_score": 0.82,
    "rerank_score": 0.89
  }$json$::jsonb,
  ARRAY['exists_subquery_not_decorrelated'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from orders where o_custkey in (select c_custkey from customer where c_phone like '139%')",
      "after_sql": "select o.* from orders o join customer c on o.o_custkey = c.c_custkey where c.c_phone like '139%'",
      "description": "当子查询结果可唯一化时，将 IN 子查询改写为 JOIN，让优化器选择更优连接顺序。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_customer_phone_custkey ON tpch.customer (c_phone, c_custkey);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 6100,
    "after_latency_ms": 380,
    "before_rows_examined": 3400000,
    "after_rows_examined": 90000
  }$json$::jsonb,
  'MySQL 8.0 / IN 子查询 / dependent subquery / 根因：未转半连接或 JOIN',
  $text$外层 orders 通过 IN 子查询匹配 customer，子查询过滤条件 c_phone 有选择性。改写为 JOIN 并补齐 customer(c_phone,c_custkey)，可提前缩小客户集合。$text$,
  0.89
),
(
  'case-mysql8-distinct-in-subquery-001',
  'mysql',
  '8.0',
  'customer-in-distinct-subquery',
  ARRAY['in_subquery', 'subquery', 'distinct'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["using_temporary"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.80,
    "vector_score": 0.77,
    "rerank_score": 0.84
  }$json$::jsonb,
  ARRAY['distinct_in_exists_subquery'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer where c_custkey in (select distinct o_custkey from orders)",
      "after_sql": "select * from customer where c_custkey in (select o_custkey from orders)",
      "description": "IN/EXISTS 只做存在性判断，子查询 DISTINCT 通常可以消除。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 3600,
    "after_latency_ms": 2100,
    "before_rows_examined": 2200000,
    "after_rows_examined": 2200000
  }$json$::jsonb,
  'MySQL 8.0 / IN 子查询 DISTINCT / Using temporary / 根因：存在性判断中冗余去重',
  $text$子查询结果只用于 IN 存在性判断，DISTINCT 会额外引入去重和临时表。删除 DISTINCT 后语义不变，减少排序或哈希去重成本。$text$,
  0.82
),
(
  'case-mysql8-or-predicate-union-001',
  'mysql',
  '8.0',
  'lineitem-or-predicate-union',
  ARRAY['or_predicate', 'where_filter', 'union'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["index_merge", "all"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "1",
    "keyword_score": 0.86,
    "vector_score": 0.82,
    "rerank_score": 0.88
  }$json$::jsonb,
  ARRAY['or_predicate_index_merge'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from lineitem where l_shipdate = date '2010-12-01' or l_partkey < 100",
      "after_sql": "select * from lineitem where l_shipdate = date '2010-12-01' union select * from lineitem where l_partkey < 100",
      "description": "OR 两侧条件都可索引时，可评估 UNION/UNION ALL 拆分，让每个分支独立使用索引。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_lineitem_shipdate ON tpch.lineitem (l_shipdate);"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_lineitem_partkey ON tpch.lineitem (l_partkey);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 9800,
    "after_latency_ms": 640,
    "before_rows_examined": 22000000,
    "after_rows_examined": 180000
  }$json$::jsonb,
  'MySQL 8.0 / OR 条件查询 / index_merge 或 ALL / 根因：OR 谓词难以稳定利用索引',
  $text$OR 条件跨两个高选择性列，优化器可能选择 index_merge 或全表扫描。拆成 UNION 后每个分支使用各自索引，适合分支结果较少的查询。$text$,
  0.87
),
(
  'case-mysql8-left-join-null-rejected-001',
  'mysql',
  '8.0',
  'orders-left-join-null-rejected',
  ARRAY['left_join', 'join', 'where_filter'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["nested_loop", "all"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.83,
    "vector_score": 0.79,
    "rerank_score": 0.85
  }$json$::jsonb,
  ARRAY['outer_join_null_rejected'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select c.c_custkey from orders o left join customer c on c.c_custkey = o.o_custkey where c.c_nationkey < 20",
      "after_sql": "select c.c_custkey from orders o inner join customer c on c.c_custkey = o.o_custkey where c.c_nationkey < 20",
      "description": "右表 WHERE 条件为空拒绝时，LEFT JOIN 可改为 INNER JOIN，让优化器更自由地选择驱动表。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_customer_nation_custkey ON tpch.customer (c_nationkey, c_custkey);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 5400,
    "after_latency_ms": 460,
    "before_rows_examined": 2800000,
    "after_rows_examined": 120000
  }$json$::jsonb,
  'MySQL 8.0 / LEFT JOIN 右表空拒绝条件 / 根因：外连接可转内连接',
  $text$WHERE c.c_nationkey < 20 会过滤掉右表为空的行，LEFT JOIN 语义等价于 INNER JOIN。改写后可以从 customer 的高选择性条件开始驱动。$text$,
  0.86
),
(
  'case-mysql8-join-elimination-001',
  'mysql',
  '8.0',
  'orders-customer-join-elimination',
  ARRAY['join', 'projection'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["ref"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.78,
    "vector_score": 0.76,
    "rerank_score": 0.82
  }$json$::jsonb,
  ARRAY['join_elimination'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select o.* from orders o inner join customer c on c.c_custkey = o.o_custkey",
      "after_sql": "select o.* from orders o where o.o_custkey is not null",
      "description": "当存在可信主外键且未引用被连接表字段时，可消除冗余 JOIN。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 2400,
    "after_latency_ms": 1500,
    "before_rows_examined": 2100000,
    "after_rows_examined": 1200000
  }$json$::jsonb,
  'MySQL 8.0 / 冗余主外键 JOIN / 根因：可连接消除',
  $text$查询只返回 orders 字段，customer 只用于主外键完整性确认。若业务和约束能保证引用完整性，JOIN 可删除，减少一次索引探测和嵌套循环成本。$text$,
  0.80
),
(
  'case-mysql8-limit-pushdown-union-001',
  'mysql',
  '8.0',
  'nation-summary-union-limit-pushdown',
  ARRAY['union', 'limit', 'order_by', 'derived_table'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["using_temporary", "using_filesort", "materialized_derived"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.82,
    "vector_score": 0.79,
    "rerank_score": 0.84
  }$json$::jsonb,
  ARRAY['limit_not_pushed_to_union'],
  $json$[
    {
      "type": "sql_rewrite",
      "description": "外层 ORDER BY LIMIT 20,10 可将每个 UNION 分支先按排序键取前 30 行，再做最终排序分页，减少派生表物化行数。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 6900,
    "after_latency_ms": 720,
    "before_rows_examined": 2600000,
    "after_rows_examined": 60000
  }$json$::jsonb,
  'MySQL 8.0 / UNION 派生表外层 LIMIT / materialized derived + filesort / 根因：LIMIT 未下推',
  $text$UNION 分支聚合后在外层排序分页，原计划需要物化大量中间结果。将 LIMIT 下推到各分支能显著减少参与最终排序的行数，适合 OFFSET 较小场景。$text$,
  0.84
),
(
  'case-mysql8-max-min-subquery-order-limit-001',
  'mysql',
  '8.0',
  'customer-max-order-subquery',
  ARRAY['max_min_subquery', 'subquery', 'aggregate', 'order_by', 'limit'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["index"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "2",
    "keyword_score": 0.81,
    "vector_score": 0.78,
    "rerank_score": 0.83
  }$json$::jsonb,
  ARRAY['max_min_aggregate_subquery'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer where c_custkey = (select max(o_custkey) from orders)",
      "after_sql": "select * from customer where c_custkey = (select o_custkey from orders order by o_custkey desc limit 1)",
      "description": "MAX/MIN 标量子查询可改为 ORDER BY + LIMIT 1，利用索引有序性避免全量聚合。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_custkey_desc ON tpch.orders (o_custkey);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 3300,
    "after_latency_ms": 18,
    "before_rows_examined": 1500000,
    "after_rows_examined": 1
  }$json$::jsonb,
  'MySQL 8.0 / MAX 标量子查询 / 根因：可利用索引有序性',
  $text$子查询只需要最大 o_custkey。如果 o_custkey 有索引，ORDER BY o_custkey DESC LIMIT 1 可以直接取索引末端，避免扫描全部行做 MAX 聚合。$text$,
  0.86
),
(
  'case-mysql8-projection-pushdown-derived-001',
  'mysql',
  '8.0',
  'derived-table-projection-pushdown',
  ARRAY['derived_table', 'projection', 'group_by', 'aggregate'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["materialized_derived", "using_temporary"],
    "estimated_rows_bucket": "1m-10m",
    "tables_count_bucket": "1",
    "keyword_score": 0.79,
    "vector_score": 0.76,
    "rerank_score": 0.82
  }$json$::jsonb,
  ARRAY['projection_not_pushed_down'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select count(1) from (select c_custkey, avg(age) from customer group by c_custkey) as d",
      "after_sql": "select count(1) from (select 1 from customer group by c_custkey) as d",
      "description": "外层只做 COUNT 时，派生表不需要输出未使用列，投影下推可减少中间结果宽度。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 4500,
    "after_latency_ms": 2600,
    "before_rows_examined": 1500000,
    "after_rows_examined": 1500000
  }$json$::jsonb,
  'MySQL 8.0 / 派生表投影过宽 / materialized derived / 根因：投影未下推',
  $text$外层只统计派生表行数，内层却计算并输出 avg(age)。删除未使用投影可以降低临时表宽度、内存和网络成本。$text$,
  0.81
),
(
  'case-mysql8-invalid-null-comparison-001',
  'mysql',
  '8.0',
  'invalid-null-comparison',
  ARRAY['where_filter', 'null_check'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": [],
    "estimated_rows_bucket": "100k-1m",
    "tables_count_bucket": "1",
    "keyword_score": 0.85,
    "vector_score": 0.81,
    "rerank_score": 0.88
  }$json$::jsonb,
  ARRAY['invalid_null_comparison'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer where c_phone = null",
      "after_sql": "select * from customer where c_phone is null",
      "description": "= NULL 永远不为真，应改为 IS NULL；CASE 中判断 NULL 也应使用 searched CASE 写法。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 120,
    "after_latency_ms": 95,
    "before_rows_examined": 0,
    "after_rows_examined": 1200
  }$json$::jsonb,
  'MySQL 8.0 / = NULL 错误判断 / 根因：NULL 比较语义错误',
  $text$SQL 使用 = NULL 判断空值，结果永远不符合预期。改为 IS NULL 后语义正确，也便于使用普通空值过滤索引策略。$text$,
  0.90
),
(
  'case-mysql8-all-subquery-minmax-null-safe-001',
  'mysql',
  '8.0',
  'customer-regdate-all-subquery-null-safe',
  ARRAY['any_all_subquery', 'subquery', 'null_check'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": ["dependent_subquery"],
    "estimated_rows_bucket": "100k-1m",
    "tables_count_bucket": "2",
    "keyword_score": 0.82,
    "vector_score": 0.80,
    "rerank_score": 0.86
  }$json$::jsonb,
  ARRAY['all_subquery_null_semantics', 'max_min_aggregate_subquery'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select * from customer where c_regdate > all(select o_orderdate from orders)",
      "after_sql": "select * from customer where c_regdate > (select max(o_orderdate) from orders)",
      "description": "ALL 子查询存在 NULL 语义风险，可用 MAX/MIN 标量子查询表达边界值，并结合非空列或 IS NOT NULL 约束校验语义。"
    },
    {
      "type": "add_index",
      "ddl": "CREATE INDEX idx_orders_orderdate ON tpch.orders (o_orderdate);"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 3800,
    "after_latency_ms": 40,
    "before_rows_examined": 1500000,
    "after_rows_examined": 1
  }$json$::jsonb,
  'MySQL 8.0 / ALL 子查询 / dependent subquery / 根因：NULL 语义风险 + 可改写为边界值查询',
  $text$ALL 修饰子查询在子查询结果含 NULL 时容易出现非预期结果。若业务语义是大于所有订单日期，可改为 MAX(o_orderdate)，并让索引有序性降低聚合成本。$text$,
  0.88
),
(
  'case-mysql8-aggregate-npe-ifnull-001',
  'mysql',
  '8.0',
  'sum-avg-null-npe-ifnull',
  ARRAY['aggregate', 'null_check'],
  $json${
    "sql_type": "select",
    "plan_symptom_tags": [],
    "estimated_rows_bucket": "100k-1m",
    "tables_count_bucket": "1",
    "keyword_score": 0.78,
    "vector_score": 0.75,
    "rerank_score": 0.80
  }$json$::jsonb,
  ARRAY['npe_aggregate'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "select sum(t.amount) from trade_detail t where t.tenant_id = 10001",
      "after_sql": "select ifnull(sum(t.amount), 0) from trade_detail t where t.tenant_id = 10001",
      "description": "SUM/AVG 在没有有效输入或输入全为 NULL 时返回 NULL，下游数值逻辑需要 0 时应显式 IFNULL/COALESCE。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 180,
    "after_latency_ms": 180,
    "before_rows_examined": 120000,
    "after_rows_examined": 120000
  }$json$::jsonb,
  'MySQL 8.0 / SUM 聚合返回 NULL / 根因：聚合空值语义导致应用 NPE 风险',
  $text$SUM/AVG 对全 NULL 输入返回 NULL，不是 0。报表或接口如果要求数值类型稳定返回，应在 SQL 层使用 IFNULL 或 COALESCE 明确兜底。$text$,
  0.82
),
(
  'case-mysql8-delete-all-to-truncate-001',
  'mysql',
  '8.0',
  'delete-all-to-truncate',
  ARRAY['delete_all'],
  $json${
    "sql_type": "delete",
    "plan_symptom_tags": ["all"],
    "estimated_rows_bucket": "10m+",
    "tables_count_bucket": "1",
    "keyword_score": 0.83,
    "vector_score": 0.78,
    "rerank_score": 0.85
  }$json$::jsonb,
  ARRAY['delete_all_without_truncate'],
  $json$[
    {
      "type": "sql_rewrite",
      "before_sql": "delete from lineitem",
      "after_sql": "truncate table lineitem",
      "description": "确认全表数据不需要保留且可接受 DDL 语义时，用 TRUNCATE 替代无条件 DELETE。"
    }
  ]$json$::jsonb,
  $json${
    "before_latency_ms": 120000,
    "after_latency_ms": 900,
    "before_rows_examined": 22000000,
    "after_rows_examined": 0
  }$json$::jsonb,
  'MySQL 8.0 / 无条件 DELETE 大表 / 根因：逐行删除日志成本高',
  $text$无条件 DELETE 会逐行记录 undo/redo/binlog 并长时间持锁。若业务确认清空全表且不需要逐行触发器语义，可在维护窗口使用 TRUNCATE。$text$,
  0.87
)
ON CONFLICT (case_id) DO UPDATE SET
  db_type = EXCLUDED.db_type,
  db_version = EXCLUDED.db_version,
  sql_fingerprint = EXCLUDED.sql_fingerprint,
  scenario_tags = EXCLUDED.scenario_tags,
  plan_features = EXCLUDED.plan_features,
  root_cause_tags = EXCLUDED.root_cause_tags,
  optimization_actions = EXCLUDED.optimization_actions,
  before_after_metrics = EXCLUDED.before_after_metrics,
  case_card = EXCLUDED.case_card,
  full_text = EXCLUDED.full_text,
  quality_score = EXCLUDED.quality_score;

COMMIT;

-- 导入后建议执行：
-- 1. python scripts/backfill_case_embeddings.py --limit 100
-- 2. 重启 chatdba 服务
--
-- 可用于验证命中的示例 SQL：
-- A. select o.id, o.user_id, o.created_at
--    from shop.orders o
--    join shop.users u on u.id = o.user_id
--    where o.tenant_id = 10001 and o.status = 1
--    order by o.created_at desc
--    limit 20;
--
-- B. select id, tenant_id, created_at
--    from shop.orders
--    where tenant_id = 10001
--    order by created_at desc
--    limit 100000, 20;
--
-- C. select c.id, c.mobile
--    from member.customer c
--    where c.mobile = 13800138000
--    order by c.id desc;
--
-- D. select * from customer c
--    where (select count(*) from orders o where o.o_custkey = c.c_custkey) > 0;
--
-- E. select * from orders
--    where adddate(o_orderdate, interval 31 day) = date '2019-10-10';
--
-- F. select c_custkey, count(*) from customer
--    group by c_custkey
--    having c_custkey < 100;
