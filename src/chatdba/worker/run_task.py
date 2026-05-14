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
        progress_sink("正在解析 SQL... ")
    graph = build_sql_optimization_graph(
        collector=collector,
        report_composer=report_composer,
    )
    result = graph.invoke(task_payload)
    if progress_sink:
        if "findings" in result:
            progress_sink("已生成诊断结论... ")
        if "report" in result:
            progress_sink("已生成优化报告...\n\n")
    return result
