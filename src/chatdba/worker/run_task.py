from chatdba.workflow.sql_optimization import build_sql_optimization_graph


def run_sql_optimization_task(task_payload: dict[str, object], collector) -> dict[str, object]:
    graph = build_sql_optimization_graph(collector=collector)
    return graph.invoke(task_payload)
