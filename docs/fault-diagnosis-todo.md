# 智能诊断 TODO

1. 诊断证据质量可观测（已完成）

   已区分 MCP 失败、HTTP 兜底失败、PromQL 无数据、CMDB 未命中、慢日志库无记录等情况，并在任务事件和报告中输出结构化诊断信息。

2. TopSQL 与指标时间线对齐

   当前 TopSQL 是告警窗口聚合结果，CPU 和活跃线程数是时序指标。要匹配峰值，需要把慢日志也转换成可对齐的时间序列：

   - 先从 CPU、活跃线程数时序中找峰值时间点，例如 `cpu_peak_at`、`active_threads_peak_at`。
   - 再把慢日志查询从整窗聚合 TopSQL 扩展为按小时间桶聚合，例如 1 分钟或 5 分钟一桶，输出 `bucket_start`、`bucket_end`、`checksum`、`sample`、`query_time_sum`、`ts_cnt`。
   - 最后以峰值时间点为中心取邻近窗口，例如峰值前后 5 分钟或 10 分钟，计算每条 SQL 在该邻近窗口内的 `query_time_sum`、`ts_cnt`、平均耗时和占比。
   - 报告中优先展示峰值邻近窗口贡献最高的 SQL；如果只有整窗聚合数据，则明确标注“只能说明窗口内相关，不能证明峰值时刻相关”。

3. 报告格式代码托管

   大模型只生成原因判断和建议文本，最终 Markdown 章节、标题、TopSQL 展示、缺失证据说明全部由代码拼装，彻底避免模型新增附录、摘要表或不稳定格式。

4. 智能诊断任务可追踪页面/API

   提供任务详情查询能力，展示原始输入、解析结果、CMDB 映射、PromQL、TopSQL SQL、每个步骤耗时、token 用量、失败原因和最终报告。

5. 配置健康检查

   增加 `chatdba doctor` 或 `/diagnosis/health`，检查 Qwen、元数据库、CMDB 表、慢日志库、Prometheus MCP、HTTP 兜底和钉钉 webhook 是否可用。

6. SQL 优化与故障诊断联动

   诊断报告发现 TopSQL 后，支持继续优化第 1 条或第 2 条 SQL，直接复用 SQL 优化链路获取表结构、执行计划、索引和改写建议。

7. 告警去重和抑制

   对 binlog 触发的告警增加去重窗口，例如同一管理 IP、指标名、实例在 10 分钟内只触发一次诊断，避免刷群和重复消耗 token。
