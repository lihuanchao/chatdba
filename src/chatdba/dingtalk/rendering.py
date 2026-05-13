from chatdba.domain.report_schema import OptimizationReport


def render_report_for_dingtalk(report: OptimizationReport) -> str:
    lines = [
        "# SQL优化报告",
        "",
        "## 任务信息",
        f"- 证据级别：`{report.evidence_status.value}`",
        f"- 置信度：`{report.confidence_label.value}` ({report.confidence:.2f})",
        "",
        "## 结论摘要",
        report.summary,
    ]
    if report.missing_evidence:
        lines.extend(
            [
                "",
                "## 缺失证据",
                ", ".join(report.missing_evidence),
            ]
        )
    if report.limitations:
        lines.extend(["", "## 限制说明"])
        for limitation in report.limitations:
            lines.append(f"- {limitation}")

    lines.extend(["", "## SQL重写建议"])
    if report.sql_rewrites:
        for rewrite in report.sql_rewrites:
            lines.append(f"- {rewrite.title}")
            lines.append("")
            lines.append("```sql")
            lines.append(rewrite.sql)
            lines.append("```")
    else:
        lines.append("- 暂无可自动重写的 SQL，请结合业务语义人工确认。")

    lines.extend(["", "## 索引推荐"])
    if report.index_recommendations:
        for recommendation in report.index_recommendations:
            lines.append(f"- 风险等级：`{recommendation.risk}`")
            lines.append("")
            lines.append("```sql")
            lines.append(recommendation.ddl)
            lines.append("```")
    else:
        lines.append("- 暂无明确索引建议，请结合 WHERE/JOIN/ORDER BY 列人工评估。")

    if report.similar_cases:
        lines.extend(["", "## 相似案例"])
        for case in report.similar_cases:
            lines.append(f"- `{case.case_id}`：{case.reason}")

    return "\n".join(lines)
