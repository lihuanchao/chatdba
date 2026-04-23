from chatdba.db.mysql_collector import MysqlEvidence, MysqlTableTarget
from chatdba.workflow.sql_optimization import build_sql_optimization_graph


class FakeCollector:
    def collect(self, sql, tables: list[MysqlTableTarget]):
        return MysqlEvidence(
            explain_json={
                "query_block": {
                    "table": {
                        "table_name": "orders",
                        "access_type": "ALL",
                        "rows_examined_per_scan": 20000,
                    }
                }
            },
            create_tables={"shop.orders": "CREATE TABLE orders (id bigint primary key)"},
        )


def test_workflow_returns_report_payload():
    graph = build_sql_optimization_graph(collector=FakeCollector())

    result = graph.invoke(
        {
            "task_id": "task-1",
            "raw_sql": "select * from orders",
            "default_schema": "shop",
        }
    )

    assert result["task_id"] == "task-1"
    assert result["findings"][0].code == "full_table_scan"
