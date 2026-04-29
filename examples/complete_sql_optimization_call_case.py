from chatdba.cases.repository import OptimizationCase
from chatdba.dingtalk.rendering import render_report_for_dingtalk
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SourceRoute
from chatdba.workflow.report_builder import OptimizationReportComposer
from chatdba.worker.run_task import run_sql_optimization_task


DEMO_SQL = """
SELECT
  o.id,
  o.order_no,
  o.amount,
  o.created_at,
  u.user_name
FROM shop.orders AS o
JOIN shop.users AS u ON u.id = o.user_id
WHERE o.tenant_id = 10001
  AND o.status = 1
ORDER BY o.created_at DESC
LIMIT 20;
""".strip()


ORDERS_CREATE_TABLE = """
CREATE TABLE `orders` (
  `id` bigint NOT NULL,
  `tenant_id` bigint NOT NULL,
  `user_id` bigint NOT NULL,
  `status` tinyint NOT NULL,
  `order_no` varchar(64) NOT NULL,
  `amount` decimal(12,2) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_orders_user_id` (`user_id`),
  KEY `idx_orders_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""".strip()


USERS_CREATE_TABLE = """
CREATE TABLE `users` (
  `id` bigint NOT NULL,
  `tenant_id` bigint NOT NULL,
  `user_name` varchar(64) NOT NULL,
  `status` tinyint NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_users_tenant_status` (`tenant_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""".strip()


DEMO_EXPLAIN_JSON = {
    "query_block": {
        "select_id": 1,
        "ordering_operation": {
            "using_filesort": True,
            "nested_loop": [
                {
                    "table": {
                        "table_name": "o",
                        "access_type": "ALL",
                        "possible_keys": ["idx_orders_user_id", "idx_orders_status"],
                        "rows_examined_per_scan": 12500000,
                        "rows_produced_per_join": 220000,
                        "filtered": 1.76,
                        "attached_condition": "((o.tenant_id = 10001) and (o.status = 1))",
                    }
                },
                {
                    "table": {
                        "table_name": "u",
                        "access_type": "eq_ref",
                        "possible_keys": ["PRIMARY"],
                        "key": "PRIMARY",
                        "used_key_parts": ["id"],
                        "rows_examined_per_scan": 1,
                    }
                },
            ],
        },
    }
}


DEMO_CASES = [
    OptimizationCase(
        case_id="case-mysql8-order-list-filesort-001",
        db_type="mysql",
        db_version_major="8.0",
        sql_type="select",
        workload_type="oltp",
        scenario_tags=["join", "order_by", "limit"],
        plan_symptom_tags=["all", "using_filesort"],
        root_cause_tags=["missing_composite_index"],
        action_tags=["add_composite_index", "sql_rewrite"],
        estimated_rows_bucket="10m+",
        case_card=(
            "MySQL 8.0 / 订单列表 JOIN + ORDER BY + LIMIT / 执行计划出现 ALL + "
            "Using filesort / 根因：缺少匹配过滤与排序的联合索引 / 优化后 18s -> 120ms"
        ),
        full_text=(
            "订单列表按 tenant_id、status 过滤后按 created_at 倒序取前 N 条。"
            "原 SQL 只能走 status 单列索引或全表扫描，排序阶段触发 filesort。"
            "新增 orders(tenant_id, status, created_at, user_id) 后，过滤、排序与回表成本显著下降。"
        ),
        keyword_score=0.92,
        vector_score=0.86,
        rerank_score=0.94,
        quality_score=0.95,
    )
]


class DemoCollector:
    def collect(self, sql: str, tables: list[object]) -> EvidenceEnvelope:
        return EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            route=SourceRoute(
                instance_id="mysql-shop-prod-01",
                db_type="mysql",
                version="8.0.36",
                host="127.0.0.1",
                port=3306,
                default_schema="shop",
                schema_names=["shop"],
            ),
            explain_json=DEMO_EXPLAIN_JSON,
            create_tables={
                "shop.orders": ORDERS_CREATE_TABLE,
                "shop.users": USERS_CREATE_TABLE,
            },
        )


def main() -> None:
    result = run_sql_optimization_task(
        {
            "task_id": "demo-order-list-filesort",
            "raw_sql": DEMO_SQL,
            "default_schema": "shop",
        },
        collector=DemoCollector(),
        report_composer=OptimizationReportComposer(cases=DEMO_CASES),
    )
    print(render_report_for_dingtalk(result["report"]))


if __name__ == "__main__":
    main()
