# ChatDBA Hybrid Case Retrieval Design

## Goal

在现有结构化案例检索的基础上，为 SQL 优化报告增加可选的 `pgvector` 向量召回能力，同时保持未配置向量能力时的稳定降级。

## Scope

- 保留当前内存案例加载与规则检索路径。
- 新增通义千问 embedding 调用能力。
- 新增基于 PostgreSQL `pgvector` 的案例 TopK 召回。
- 将向量召回结果与现有规则检索结果做融合重排。
- 提供历史案例 embedding 回填脚本。

## Non-Goals

- 本次不接入 OpenSearch/BM25。
- 本次不做在线案例写回和自动 embedding 更新。
- 本次不改动报告 schema。

## Design

### Retrieval Flow

1. 启动时仍从 `optimization_cases` 加载结构化案例到内存。
2. 报告生成前构造检索画像：
   - `db_type`
   - `db_version_major`
   - `sql_type`
   - `scenario_tags`
   - `plan_symptom_tags`
   - `root_cause_tags`
   - `embedding_text`
3. 先跑现有规则检索，拿到规则候选集。
4. 如果已配置 embedding 且案例表存在 embedding：
   - 调用通义千问 embedding 生成查询向量。
   - 用 `pgvector` 在 `optimization_cases` 中做 TopK 向量召回。
   - 将向量召回候选与规则候选做并集。
5. 对候选集执行最终规则重排，并把向量相似度作为查询时覆盖分数参与排序。
6. 如果 embedding 或 pgvector 查询失败，自动退回现有规则检索。

### Boundaries

- `QwenGateway`：负责报告生成和 embedding 生成。
- `PgVectorCaseRetriever`：负责向量召回和与规则检索融合。
- `OptimizationReportComposer`：只感知“可选混合检索器”，不直接处理数据库细节。

### Backfill

提供单独脚本遍历 `optimization_cases`，基于案例文本生成 embedding 并回写数据库。脚本支持仅补齐空 embedding，避免重复覆盖。

## Testing

- `QwenGateway` embedding 调用单测。
- `PgVectorCaseRetriever` 融合与降级单测。
- `OptimizationReportComposer` 使用混合检索器单测。
- 全量回归测试。
