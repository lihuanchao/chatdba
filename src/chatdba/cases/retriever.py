from chatdba.cases.repository import OptimizationCase


def retrieve_cases(
    cases: list[OptimizationCase],
    db_type: str,
    scenario_tags: list[str],
    limit: int = 5,
) -> list[OptimizationCase]:
    wanted_tags = set(scenario_tags)
    filtered = [
        case
        for case in cases
        if case.db_type == db_type and (not wanted_tags or wanted_tags.intersection(case.scenario_tags))
    ]
    return sorted(filtered, key=lambda case: case.quality_score, reverse=True)[:limit]
