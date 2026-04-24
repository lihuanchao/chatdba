from chatdba.domain.models import PlanFeature, RuleFinding, SqlFeatures


def run_mysql_rules(sql_features: SqlFeatures, plan_features: list[PlanFeature]) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for feature in plan_features:
        if feature.code == "full_table_scan":
            findings.append(
                RuleFinding(
                    code="full_table_scan",
                    severity=feature.severity,
                    message="执行计划显示大表全表扫描，缺少可用索引访问路径。",
                    evidence=feature.evidence,
                )
            )
    if sql_features.has_limit and sql_features.order_by:
        findings.append(
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="LIMIT + ORDER BY 场景建议检查是否存在匹配排序的索引。",
                evidence={"order_by": sql_features.order_by},
            )
        )
    return findings
