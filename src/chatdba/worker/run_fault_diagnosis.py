from collections.abc import Callable

from chatdba.workflow.fault_diagnosis import (
    CmdbResolver,
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
    cmdb_resolver: CmdbResolver | None = None,
    qwen_gateway=None,
    progress_sink: ProgressSink | None = None,
) -> dict[str, object]:
    graph = build_fault_diagnosis_graph(
        top_sql_agent=top_sql_agent,
        metric_agent=metric_agent,
        cmdb_resolver=cmdb_resolver,
        qwen_gateway=qwen_gateway,
    )
    result = graph.invoke(task_payload)
    if progress_sink:
        progress_sink("正在生成故障诊断报告...\n")
    return result
