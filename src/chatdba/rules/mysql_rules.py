from chatdba.domain.models import PlanFeature, RuleFinding, SqlFeatures


def run_mysql_rules(sql_features: SqlFeatures, plan_features: list[PlanFeature]) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for feature in plan_features:
        if feature.code == "full_table_scan":
            findings.append(
                RuleFinding(
                    code="full_table_scan",
                    severity=feature.severity,
                    message="The execution plan scans a large table without an index access path.",
                    evidence=feature.evidence,
                )
            )
    if sql_features.has_limit and sql_features.order_by:
        findings.append(
            RuleFinding(
                code="limit_with_order_by",
                severity="medium",
                message="LIMIT with ORDER BY should be checked for a supporting index.",
                evidence={"order_by": sql_features.order_by},
            )
        )
    return findings
