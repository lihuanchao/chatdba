from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


class AmbiguousTableCollector:
    def collect(self, sql, tables):
        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=["以下表名在元数据库中存在重复，请补充库名后重试：orders"],
        )


class MultiInstanceRouteCollector:
    def collect(self, sql, tables):
        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=[
                "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"
            ],
        )


class MultiTableSchemaRouteCollector:
    def collect(self, sql, tables):
        return EvidenceEnvelope(
            status=EvidenceStatus.SQL_ONLY,
            missing_evidence=["route_info", "explain_json", "create_table"],
            collection_errors=[
                "SQL 多表关联无法唯一确定数据库，请补充库名后重试：orders, users"
            ],
        )


def test_sql_optimization_graph_stops_when_table_name_requires_schema():
    graph = build_sql_optimization_graph(collector=AmbiguousTableCollector())

    result = graph.invoke(
        {
            "task_id": "task-1",
            "raw_sql": "select * from orders",
        }
    )

    assert result["evidence"].status == EvidenceStatus.SQL_ONLY
    assert "请补充库名" in result["evidence"].collection_errors[0]
    assert "findings" not in result
    assert "report" not in result


def test_sql_optimization_graph_stops_when_join_tables_need_schema():
    graph = build_sql_optimization_graph(collector=MultiTableSchemaRouteCollector())

    result = graph.invoke(
        {
            "task_id": "task-1",
            "raw_sql": "select * from orders join users on orders.user_id = users.id",
        }
    )

    assert result["evidence"].status == EvidenceStatus.SQL_ONLY
    assert "请补充库名" in result["evidence"].collection_errors[0]
    assert "findings" not in result
    assert "report" not in result


def test_sql_optimization_graph_stops_when_route_spans_multiple_instances():
    graph = build_sql_optimization_graph(collector=MultiInstanceRouteCollector())

    result = graph.invoke(
        {
            "task_id": "task-1",
            "raw_sql": "select * from orders",
        }
    )

    assert result["evidence"].status == EvidenceStatus.SQL_ONLY
    assert "多个源实例" in result["evidence"].collection_errors[0]
    assert "findings" not in result
    assert "report" not in result
