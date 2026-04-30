from chatdba.cases.repository import OptimizationCase
from chatdba.cases.retriever import CaseRetrievalQuery
from chatdba.domain.report_schema import SimilarCase


def similar_cases_for_report(
    cases: list[OptimizationCase],
    query: CaseRetrievalQuery | None = None,
) -> list[SimilarCase]:
    return [
        SimilarCase(case_id=case.case_id, reason=case_match_reason(case, query))
        for case in cases
    ]


def case_match_reason(
    case: OptimizationCase,
    query: CaseRetrievalQuery | None,
) -> str:
    if query is None:
        return case.case_card

    parts: list[str] = []
    root_cause_hits = _ordered_overlap(query.root_cause_tags, case.root_cause_tags)
    if root_cause_hits:
        parts.append(f"根因标签命中：{', '.join(root_cause_hits)}")

    plan_hits = _ordered_overlap(query.plan_symptom_tags, case.plan_symptom_tags)
    if plan_hits:
        parts.append(f"执行计划症状命中：{', '.join(plan_hits)}")

    scenario_hits = _ordered_overlap(query.scenario_tags, case.scenario_tags)
    if scenario_hits:
        parts.append(f"SQL场景命中：{', '.join(scenario_hits)}")

    if query.db_version_major and case.db_version_major == query.db_version_major:
        parts.append(f"数据库版本匹配：{case.db_version_major}")

    parts.append(f"案例摘要：{case.case_card}")
    return "；".join(parts)


def _ordered_overlap(wanted: list[str], actual: list[str]) -> list[str]:
    actual_tags = {_normalize_tag(tag) for tag in actual}
    hits: list[str] = []
    for tag in wanted:
        normalized = _normalize_tag(tag)
        if normalized in actual_tags and normalized not in hits:
            hits.append(normalized)
    return hits


def _normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" ", "_")
