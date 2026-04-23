from collections.abc import Iterator
from typing import Any

from chatdba.domain.models import PlanFeature


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if "access_type" in node:
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def extract_plan_features(explain_json: dict[str, object]) -> list[PlanFeature]:
    features: list[PlanFeature] = []
    for table_node in _walk(explain_json):
        access_type = table_node.get("access_type")
        rows = int(table_node.get("rows_examined_per_scan") or table_node.get("rows_produced_per_join") or 0)
        if access_type == "ALL" and rows >= 10000:
            features.append(
                PlanFeature(
                    code="full_table_scan",
                    severity="high",
                    evidence={"table": table_node.get("table_name"), "rows": rows},
                )
            )
    return features

