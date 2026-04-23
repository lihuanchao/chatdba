from chatdba.domain.models import PlanFeature, SqlFeatures
from chatdba.rules.mysql_rules import run_mysql_rules


def test_rules_convert_full_scan_feature_to_finding():
    findings = run_mysql_rules(
        SqlFeatures(fingerprint="abc", statement_type="select"),
        [PlanFeature(code="full_table_scan", severity="high", evidence={"table": "orders"})],
    )

    assert findings[0].code == "full_table_scan"
    assert findings[0].severity == "high"


def test_rules_emit_limit_with_order_by_finding():
    findings = run_mysql_rules(
        SqlFeatures(
            fingerprint="abc",
            statement_type="select",
            has_limit=True,
            order_by=["created_at DESC"],
        ),
        [],
    )

    assert findings[0].code == "limit_with_order_by"
    assert findings[0].severity == "medium"
    assert findings[0].evidence == {"order_by": ["created_at DESC"]}
