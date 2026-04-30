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
  ARRAY['order_by'],
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

COMMIT;

-- 导入后建议执行：
-- 1. python scripts/backfill_case_embeddings.py --limit 100
-- 2. 重启 chatdba 服务
--
-- 可用于验证命中的示例 SQL：
A. select o.id, o.user_id, o.created_at
   from shop.orders o
   join shop.users u on u.id = o.user_id
   where o.tenant_id = 10001 and o.status = 1
   order by o.created_at desc
   limit 20;
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
