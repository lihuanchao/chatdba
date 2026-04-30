from collections.abc import Callable

from chatdba.workflow.fault_diagnosis import (
    MetricAgent,
    TopSqlAgent,
    build_fault_diagnosis_graph,
)

ProgressSink = Callable[[str], None]


def run_fault_diagnosis_task(
    task_payload: dict[str, object],
    *,
    top_sql_agent: TopSqlAgent | None = None,
    metric_agent: MetricAgent | None = None,
    qwen_gateway=None,
    progress_sink: ProgressSink | None = None,
) -> dict[str, object]:
    if progress_sink:
        progress_sink("正在解析故障信息...\n")
    graph = build_fault_diagnosis_graph(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
        qwen_gateway=qwen_gateway,
    )
    if progress_sink:
        progress_sink("正在获取 TopSQL...\n")
        progress_sink("正在获取监控指标...\n")
        progress_sink("正在生成故障诊断报告...\n")
    return graph.invoke(task_payload)
