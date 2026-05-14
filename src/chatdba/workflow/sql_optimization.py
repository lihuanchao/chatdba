from langgraph.graph import END, StateGraph

from chatdba.db.metadata_repository import StaticMetadataRepository
from chatdba.db.route_errors import is_route_resolution_blocker
from chatdba.explain.mysql_json import extract_plan_features
from chatdba.rules.mysql_rules import run_mysql_rules
from chatdba.sql.parser import parse_sql_features
from chatdba.sql.schema_qualification import qualify_unqualified_tables, unqualified_table_names
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.workflow.state import SqlOptimizationState


def build_sql_optimization_graph(
    collector,
    report_composer: OptimizationReportComposer | None = None,
):
    graph = StateGraph(SqlOptimizationState)
    composer = report_composer or OptimizationReportComposer(cases=[])

    def parse_sql(state: SqlOptimizationState) -> SqlOptimizationState:
        return {"sql_features": parse_sql_features(state["raw_sql"])}

    def collect_evidence(state: SqlOptimizationState) -> SqlOptimizationState:
        sql_features = state["sql_features"]
        resolver = StaticMetadataRepository(
            default_schema=state.get("default_schema", "default")
        )
        targets = resolver.resolve_tables(sql_features.tables)
        schema_name = state.get("schema_name")
        evidence_sql = state["raw_sql"]
        if schema_name:
            table_names = unqualified_table_names(evidence_sql)
            if table_names:
                evidence_sql = qualify_unqualified_tables(
                    evidence_sql,
                    schema_name=schema_name,
                    table_names=table_names,
                )
                targets = resolver.resolve_tables(parse_sql_features(evidence_sql).tables)
        return {"evidence": collector.collect(evidence_sql, targets)}

    def route_after_evidence(state: SqlOptimizationState) -> str:
        evidence = state["evidence"]
        if any(
            is_route_resolution_blocker(error)
            for error in evidence.collection_errors
        ):
            return "end"
        return "diagnose"

    def diagnose(state: SqlOptimizationState) -> SqlOptimizationState:
        explain_json = state["evidence"].explain_json or {}
        plan_features = extract_plan_features(explain_json) if explain_json else []
        findings = run_mysql_rules(state["sql_features"], plan_features)
        return {"findings": findings}

    def build_report(state: SqlOptimizationState) -> SqlOptimizationState:
        report = composer.compose(
            task_id=state["task_id"],
            raw_sql=state["raw_sql"],
            sql_features=state["sql_features"],
            evidence=state["evidence"],
            findings=state["findings"],
        )
        return {"report": report}

    graph.add_node("parse_sql", parse_sql)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("diagnose", diagnose)
    graph.add_node("build_report", build_report)
    graph.set_entry_point("parse_sql")
    graph.add_edge("parse_sql", "collect_evidence")
    graph.add_conditional_edges(
        "collect_evidence",
        route_after_evidence,
        {
            "diagnose": "diagnose",
            "end": END,
        },
    )
    graph.add_edge("diagnose", "build_report")
    graph.add_edge("build_report", END)
    return graph.compile()
