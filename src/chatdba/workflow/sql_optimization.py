from langgraph.graph import END, StateGraph

from chatdba.db.metadata_repository import StaticMetadataRepository
from chatdba.explain.mysql_json import extract_plan_features
from chatdba.rules.mysql_rules import run_mysql_rules
from chatdba.sql.parser import parse_sql_features
from chatdba.workflow.state import SqlOptimizationState


def build_sql_optimization_graph(collector):
    graph = StateGraph(SqlOptimizationState)

    def parse_sql(state: SqlOptimizationState) -> SqlOptimizationState:
        return {"sql_features": parse_sql_features(state["raw_sql"])}

    def collect_evidence(state: SqlOptimizationState) -> SqlOptimizationState:
        sql_features = state["sql_features"]
        resolver = StaticMetadataRepository(
            default_schema=state.get("default_schema", "default")
        )
        targets = resolver.resolve_tables(sql_features.tables)
        return {"evidence": collector.collect(state["raw_sql"], targets)}

    def diagnose(state: SqlOptimizationState) -> SqlOptimizationState:
        plan_features = extract_plan_features(state["evidence"].explain_json)
        findings = run_mysql_rules(state["sql_features"], plan_features)
        return {"findings": findings}

    graph.add_node("parse_sql", parse_sql)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("diagnose", diagnose)
    graph.set_entry_point("parse_sql")
    graph.add_edge("parse_sql", "collect_evidence")
    graph.add_edge("collect_evidence", "diagnose")
    graph.add_edge("diagnose", END)
    return graph.compile()
