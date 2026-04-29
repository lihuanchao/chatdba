from chatdba.cases.repository import OptimizationCase


def retrieve_cases(
    cases: list[OptimizationCase],
    db_type: str,
    scenario_tags: list[str],
    db_version_major: str | None = None,
    sql_type: str | None = None,
    plan_symptom_tags: list[str] | None = None,
    root_cause_tags: list[str] | None = None,
    action_tags: list[str] | None = None,
    estimated_rows_bucket: str | None = None,
    tables_count_bucket: str | None = None,
    workload_type: str | None = None,
    limit: int = 5,
    max_same_root_cause: int = 2,
) -> list[OptimizationCase]:
    profile = _RetrievalProfile(
        db_type=_normalize_scalar(db_type),
        db_version_major=_normalize_optional(db_version_major),
        sql_type=_normalize_optional(sql_type),
        scenario_tags=_normalize_set(scenario_tags),
        plan_symptom_tags=_normalize_set(plan_symptom_tags or []),
        root_cause_tags=_normalize_set(root_cause_tags or []),
        action_tags=_normalize_set(action_tags or []),
        estimated_rows_bucket=_normalize_optional(estimated_rows_bucket),
        tables_count_bucket=_normalize_optional(tables_count_bucket),
        workload_type=_normalize_optional(workload_type),
    )
    rescored = [
        (case, _score_case(profile, case))
        for case in cases
        if _passes_hard_filters(profile, case)
    ]
    ranked = [
        case
        for case, _score in sorted(
            rescored,
            key=lambda item: (item[1], item[0].quality_score),
            reverse=True,
        )
    ]
    return _dedupe_by_root_cause(ranked, limit=limit, max_same_root_cause=max_same_root_cause)


class _RetrievalProfile:
    def __init__(
        self,
        *,
        db_type: str,
        db_version_major: str | None,
        sql_type: str | None,
        scenario_tags: set[str],
        plan_symptom_tags: set[str],
        root_cause_tags: set[str],
        action_tags: set[str],
        estimated_rows_bucket: str | None,
        tables_count_bucket: str | None,
        workload_type: str | None,
    ) -> None:
        self.db_type = db_type
        self.db_version_major = db_version_major
        self.sql_type = sql_type
        self.scenario_tags = scenario_tags
        self.plan_symptom_tags = plan_symptom_tags
        self.root_cause_tags = root_cause_tags
        self.action_tags = action_tags
        self.estimated_rows_bucket = estimated_rows_bucket
        self.tables_count_bucket = tables_count_bucket
        self.workload_type = workload_type


def _passes_hard_filters(profile: _RetrievalProfile, case: OptimizationCase) -> bool:
    if _normalize_scalar(case.db_type) != profile.db_type:
        return False

    case_version = _normalize_optional(case.db_version_major)
    if profile.db_version_major and case_version and case_version != profile.db_version_major:
        return False

    case_sql_type = _normalize_optional(case.sql_type)
    if profile.sql_type and case_sql_type and case_sql_type != profile.sql_type:
        return False

    case_scenario_tags = _normalize_set(case.scenario_tags)
    if profile.scenario_tags and case_scenario_tags and not profile.scenario_tags.intersection(case_scenario_tags):
        return False

    return True


def _score_case(profile: _RetrievalProfile, case: OptimizationCase) -> float:
    environment_score = _environment_score(profile, case)
    shape_score = _tag_overlap_score(profile.scenario_tags, _normalize_set(case.scenario_tags))
    plan_score = _plan_score(profile, case)
    root_cause_score = _tag_overlap_score(
        profile.root_cause_tags,
        _normalize_set(case.root_cause_tags),
    )
    retrieval_score = _average(
        [
            _clamp(case.keyword_score),
            _clamp(case.vector_score),
            _clamp(case.rerank_score),
        ]
    )
    quality_score = _clamp(case.quality_score)

    return (
        0.20 * environment_score
        + 0.15 * shape_score
        + 0.30 * plan_score
        + 0.20 * root_cause_score
        + 0.10 * retrieval_score
        + 0.05 * quality_score
    )


def _environment_score(profile: _RetrievalProfile, case: OptimizationCase) -> float:
    scores = [1.0 if _normalize_scalar(case.db_type) == profile.db_type else 0.0]

    if profile.db_version_major:
        scores.append(
            _optional_exact_match_score(profile.db_version_major, case.db_version_major)
        )
    if profile.sql_type:
        scores.append(_optional_exact_match_score(profile.sql_type, case.sql_type))
    if profile.workload_type:
        scores.append(_optional_exact_match_score(profile.workload_type, case.workload_type))

    return _average(scores)


def _plan_score(profile: _RetrievalProfile, case: OptimizationCase) -> float:
    scores: list[float] = []
    if profile.plan_symptom_tags:
        scores.append(
            _tag_overlap_score(
                profile.plan_symptom_tags,
                _normalize_set(case.plan_symptom_tags),
            )
        )
    if profile.estimated_rows_bucket:
        scores.append(
            _optional_exact_match_score(
                profile.estimated_rows_bucket,
                case.estimated_rows_bucket,
            )
        )
    if profile.tables_count_bucket:
        scores.append(
            _optional_exact_match_score(
                profile.tables_count_bucket,
                case.tables_count_bucket,
            )
        )
    return _average(scores) if scores else 0.0


def _tag_overlap_score(wanted: set[str], actual: set[str]) -> float:
    if not wanted:
        return 0.0
    if not actual:
        return 0.0
    return len(wanted.intersection(actual)) / len(wanted)


def _optional_exact_match_score(wanted: str, actual: str | None) -> float:
    normalized_actual = _normalize_optional(actual)
    if not normalized_actual:
        return 0.0
    return 1.0 if normalized_actual == wanted else 0.0


def _dedupe_by_root_cause(
    cases: list[OptimizationCase],
    *,
    limit: int,
    max_same_root_cause: int,
) -> list[OptimizationCase]:
    selected: list[OptimizationCase] = []
    root_cause_counts: dict[tuple[str, ...], int] = {}
    for case in cases:
        root_key = tuple(sorted(_normalize_set(case.root_cause_tags)))
        if root_key:
            count = root_cause_counts.get(root_key, 0)
            if count >= max_same_root_cause:
                continue
            root_cause_counts[root_key] = count + 1

        selected.append(case)
        if len(selected) >= limit:
            break
    return selected


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalize_set(values: list[str]) -> set[str]:
    return {_normalize_scalar(value) for value in values if _normalize_scalar(value)}


def _normalize_optional(value: str | None) -> str | None:
    normalized = _normalize_scalar(value or "")
    return normalized or None


def _normalize_scalar(value: str) -> str:
    return value.strip().lower().replace(" ", "_")
