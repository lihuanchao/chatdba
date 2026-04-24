from chatdba.domain.report_schema import OptimizationReport


def render_report_for_dingtalk(report: OptimizationReport) -> str:
    lines = [
        "SQL Optimization Report",
        f"Task: {report.task_id}",
        f"Evidence: {report.evidence_status.value.upper()}",
        f"Confidence: {report.confidence_label.value.upper()} ({report.confidence:.2f})",
        f"Summary: {report.summary}",
    ]
    if report.missing_evidence:
        lines.append("Missing: " + ", ".join(report.missing_evidence))
    if report.limitations:
        lines.append("Limitations: " + " | ".join(report.limitations))
    if report.validation_steps:
        lines.append("Validate: " + " | ".join(report.validation_steps))
    return "\n".join(lines)
