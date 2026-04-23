from collections.abc import Callable

from chatdba.workflow.sql_optimization import build_sql_optimization_graph


ProgressSink = Callable[[str], None]


def run_sql_optimization_task(
    task_payload: dict[str, object],
    collector,
    progress_sink: ProgressSink | None = None,
) -> dict[str, object]:
    if progress_sink:
        progress_sink("Parsing SQL\n")
    graph = build_sql_optimization_graph(collector=collector)
    result = graph.invoke(task_payload)
    if progress_sink:
        progress_sink("Generated diagnostic findings\n")
    return result
