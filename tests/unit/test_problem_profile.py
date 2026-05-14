from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus, SqlFeatures, TableReference
from chatdba.workflow.problem_profile import derive_problem_profile


def test_problem_profile_derives_common_rewrite_and_implicit_cast_tags():
    profile = derive_problem_profile(
        raw_sql=(
            "select * from users where user_name = 123 "
            "and deleted_at = null "
            "and exists (select count(*) from orders where orders.user_id = users.id) > 0"
        ),
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[TableReference(table_name="users")],
            predicates=[
                "user_name = 123",
                "deleted_at = null",
                "(select count(*) from orders where orders.user_id = users.id) > 0",
            ],
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            create_tables={
                "shop.users": "CREATE TABLE `users` (`user_name` varchar(64) NOT NULL)"
            },
        ),
        findings=[],
    )

    assert "where_filter" in profile.scenario_tags
    assert "equality_predicate" in profile.scenario_tags
    assert "null_check" in profile.scenario_tags
    assert "aggregate" in profile.scenario_tags
    assert "index_not_used" in profile.plan_symptom_tags
    assert "implicit_cast" in profile.root_cause_tags
    assert "invalid_null_comparison" in profile.root_cause_tags
    assert "count_subquery_to_exists" in profile.root_cause_tags


def test_problem_profile_derives_implicit_cast_for_numeric_in_list_on_string_column():
    profile = derive_problem_profile(
        raw_sql=(
            "SELECT BILL_ID, IS_DELETE FROM `international-base`.sys_file_info "
            "WHERE IS_DELETE = 0 AND (BILL_ID IN (1985623467204448257))"
        ),
        sql_features=SqlFeatures(
            fingerprint="fp",
            statement_type="select",
            tables=[
                TableReference(
                    schema_name="international-base",
                    table_name="sys_file_info",
                )
            ],
            predicates=["IS_DELETE = 0 AND (BILL_ID IN (1985623467204448257))"],
        ),
        evidence=EvidenceEnvelope(
            status=EvidenceStatus.FULL,
            create_tables={
                "international-base.sys_file_info": (
                    "CREATE TABLE `sys_file_info` ("
                    "`BILL_ID` varchar(64) NOT NULL, "
                    "`IS_DELETE` tinyint NOT NULL, "
                    "KEY `idx_bill_id` (`BILL_ID`)"
                    ")"
                )
            },
        ),
        findings=[],
    )

    assert "where_filter" in profile.scenario_tags
    assert "index_not_used" in profile.plan_symptom_tags
    assert "implicit_cast" in profile.root_cause_tags
    assert "missing_composite_index" not in profile.root_cause_tags
    assert "BILL_ID" in " ".join(profile.evidence)
