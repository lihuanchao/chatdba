from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Protocol, TypedDict

from langgraph.graph import END, StateGraph

from chatdba.domain.fault_diagnosis import (
    FaultDiagnosisProfile,
    FaultDiagnosisReport,
    FaultPlanStep,
    MetricEvidence,
    TopSqlEvidence,
)


class FaultDiagnosisGateway(Protocol):
    def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class TopSqlAgent(Protocol):
    def analyze(self, profile: FaultDiagnosisProfile) -> TopSqlEvidence:
        raise NotImplementedError


class MetricAgent(Protocol):
    def analyze(self, profile: FaultDiagnosisProfile) -> MetricEvidence:
        raise NotImplementedError


class CmdbResolver(Protocol):
    def resolve_by_management_ip(self, management_ip: str):
        raise NotImplementedError


class FaultDiagnosisState(TypedDict, total=False):
    task_id: str
    input_text: str
    current_time: datetime
    profile: FaultDiagnosisProfile
    top_sql: TopSqlEvidence
    metrics: MetricEvidence
    adjudication: str
    report: FaultDiagnosisReport


class EmptyTopSqlAgent:
    def analyze(self, profile: FaultDiagnosisProfile) -> TopSqlEvidence:
        return TopSqlEvidence(
            status="failure",
            rows=[],
            error_message="TopSQL 子 agent 未配置真实数据源。",
        )


class EmptyMetricAgent:
    def analyze(self, profile: FaultDiagnosisProfile) -> MetricEvidence:
        return MetricEvidence(
            status="failure",
            metrics=[],
            error_message="Metric 子 agent 未配置真实数据源。",
        )


def build_fault_diagnosis_graph(
    *,
    top_sql_agent: TopSqlAgent | None = None,
    metric_agent: MetricAgent | None = None,
    cmdb_resolver: CmdbResolver | None = None,
    qwen_gateway: FaultDiagnosisGateway | None = None,
):
    graph = StateGraph(FaultDiagnosisState)
    top_sql = top_sql_agent or EmptyTopSqlAgent()
    metric = metric_agent or EmptyMetricAgent()

    def parse_input(state: FaultDiagnosisState) -> FaultDiagnosisState:
        profile = _build_profile(
            input_text=state["input_text"],
            current_time=state.get("current_time"),
            cmdb_resolver=cmdb_resolver,
            qwen_gateway=qwen_gateway,
        )
        return {"profile": profile}

    def collect_top_sql(state: FaultDiagnosisState) -> FaultDiagnosisState:
        return {"top_sql": top_sql.analyze(state["profile"])}

    def collect_metrics(state: FaultDiagnosisState) -> FaultDiagnosisState:
        return {"metrics": metric.analyze(state["profile"])}

    def adjudicate(state: FaultDiagnosisState) -> FaultDiagnosisState:
        return {
            "adjudication": _adjudicate(
                profile=state["profile"],
                top_sql=state["top_sql"],
                metrics=state["metrics"],
                qwen_gateway=qwen_gateway,
            )
        }

    def build_report(state: FaultDiagnosisState) -> FaultDiagnosisState:
        report = _build_report(
            task_id=state["task_id"],
            profile=state["profile"],
            top_sql=state["top_sql"],
            metrics=state["metrics"],
            adjudication=state["adjudication"],
            qwen_gateway=qwen_gateway,
        )
        return {"report": report}

    graph.add_node("parse_input", parse_input)
    graph.add_node("collect_top_sql", collect_top_sql)
    graph.add_node("collect_metrics", collect_metrics)
    graph.add_node("adjudicate", adjudicate)
    graph.add_node("build_report", build_report)
    graph.set_entry_point("parse_input")
    graph.add_edge("parse_input", "collect_top_sql")
    graph.add_edge("collect_top_sql", "collect_metrics")
    graph.add_edge("collect_metrics", "adjudicate")
    graph.add_edge("adjudicate", "build_report")
    graph.add_edge("build_report", END)
    return graph.compile()


def _build_profile(
    *,
    input_text: str,
    current_time: datetime | None,
    cmdb_resolver: CmdbResolver | None,
    qwen_gateway: FaultDiagnosisGateway | None,
) -> FaultDiagnosisProfile:
    profile = _fallback_profile(
        input_text=input_text,
        current_time=current_time,
        cmdb_resolver=cmdb_resolver,
    )
    if qwen_gateway is None:
        return profile
    qwen_profile = _profile_with_qwen(
        input_text=input_text,
        current_time=current_time,
        cmdb_resolver=cmdb_resolver,
        qwen_gateway=qwen_gateway,
    )
    return qwen_profile or profile


def _fallback_profile(
    *,
    input_text: str,
    current_time: datetime | None,
    cmdb_resolver: CmdbResolver | None = None,
) -> FaultDiagnosisProfile:
    now = current_time or datetime.now()
    start_time, end_time = _time_window(input_text, now)
    alert_time = _extract_alert_time(input_text)
    management_ip = _extract_first_ip(input_text)
    cmdb_record = _resolve_cmdb_record(cmdb_resolver, management_ip)
    business_ip = _cmdb_value(cmdb_record, "business_ip")
    primary_ip = management_ip
    system_name = _cmdb_value(cmdb_record, "system_name")
    if not system_name:
        system_name = _extract_system_name(input_text)
    missing_fields: list[str] = []
    if not (management_ip or system_name):
        missing_fields.append("system_name_or_ip")
    if management_ip and not business_ip:
        missing_fields.append("business_ip")

    query_background = _query_background(
        input_text=input_text,
        system_name=system_name,
        primary_ip=management_ip,
        start_time=start_time,
        end_time=end_time,
    )
    date = f"{start_time} 到 {end_time} UTC+8"
    plan = [
        FaultPlanStep(
            step_id=1,
            agent="metric",
            date=date,
            query_background=query_background,
            query=(
                "通过 CMDB 将告警中的管理 IP 转换为业务 IP，"
                "再查询数据库服务器 CPU 使用率等关键监控指标。"
            ),
            reason="监控指标按业务 IP 打标，必须先完成管理 IP 到业务 IP 的映射。",
        ),
        FaultPlanStep(
            step_id=2,
            agent="top_sql",
            date=date,
            query_background=query_background,
            query="查询故障时间窗口内的 TopSQL，重点关注长时间运行 SQL 和执行中的慢 SQL。",
            reason="TopSQL 可以解释数据库 CPU 高、会话堆积和业务响应变慢等现象。",
        ),
    ]
    return FaultDiagnosisProfile(
        input_text=input_text,
        system_name=system_name,
        management_ip=management_ip,
        business_ip=business_ip,
        primary_ip=primary_ip,
        alert_time=_format_time(alert_time) if alert_time else None,
        start_time=start_time,
        end_time=end_time,
        query_background=query_background,
        plan=plan,
        missing_fields=missing_fields,
    )


def _profile_with_qwen(
    *,
    input_text: str,
    current_time: datetime | None,
    cmdb_resolver: CmdbResolver | None,
    qwen_gateway: FaultDiagnosisGateway,
) -> FaultDiagnosisProfile | None:
    fallback = _fallback_profile(
        input_text=input_text,
        current_time=current_time,
        cmdb_resolver=cmdb_resolver,
    )
    user_prompt = json.dumps(
        {
            "input_text": input_text,
            "current_time": (current_time or datetime.now()).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "fallback_profile": fallback.model_dump(mode="python"),
        },
        ensure_ascii=False,
    )
    try:
        payload = qwen_gateway.generate_report(
            "你是数据库故障诊断调度专家，请只返回 FaultDiagnosisProfile JSON。",
            user_prompt,
        )
        profile = FaultDiagnosisProfile.model_validate(json.loads(payload))
        return _merge_profile_cmdb_fields(profile, fallback)
    except Exception:
        return None


def _adjudicate(
    *,
    profile: FaultDiagnosisProfile,
    top_sql: TopSqlEvidence,
    metrics: MetricEvidence,
    qwen_gateway: FaultDiagnosisGateway | None,
) -> str:
    if qwen_gateway is None:
        return _fallback_adjudication(top_sql=top_sql, metrics=metrics)
    user_prompt = json.dumps(
        {
            "profile": profile.model_dump(mode="python"),
            "top_sql": top_sql.model_dump(mode="python"),
            "metrics": metrics.model_dump(mode="python"),
        },
        ensure_ascii=False,
    )
    try:
        adjudication = qwen_gateway.generate_report(
            "你是数据库值班长，请基于证据输出中文根因仲裁结论，禁止编造数据。",
            user_prompt,
        ).strip()
    except Exception:
        return _fallback_adjudication(top_sql=top_sql, metrics=metrics)
    return adjudication or _fallback_adjudication(top_sql=top_sql, metrics=metrics)


def _fallback_adjudication(
    *,
    top_sql: TopSqlEvidence,
    metrics: MetricEvidence,
) -> str:
    if top_sql.status == "success" and metrics.status == "success":
        return "监控指标异常与 TopSQL 证据同时存在，疑似 SQL 执行导致数据库高负载。"
    if top_sql.status == "success":
        return "存在 TopSQL 证据，但缺少监控指标佐证。"
    if metrics.status == "success":
        return "存在监控指标异常，但缺少 TopSQL 证据。"
    return "证据不足，暂无法确认根因。"


def _build_report(
    *,
    task_id: str,
    profile: FaultDiagnosisProfile,
    top_sql: TopSqlEvidence,
    metrics: MetricEvidence,
    adjudication: str,
    qwen_gateway: FaultDiagnosisGateway | None,
) -> FaultDiagnosisReport:
    fallback = _fallback_report(
        task_id=task_id,
        profile=profile,
        top_sql=top_sql,
        metrics=metrics,
        adjudication=adjudication,
    )
    if qwen_gateway is None:
        return fallback

    user_prompt = json.dumps(
        {
            "profile": profile.model_dump(mode="python"),
            "top_sql": top_sql.model_dump(mode="python"),
            "metrics": metrics.model_dump(mode="python"),
            "adjudication": adjudication,
            "fallback_report": fallback.model_dump(mode="python"),
        },
        ensure_ascii=False,
    )
    try:
        markdown = qwen_gateway.generate_report(
            "你是数据库AIOps根因分析运营专家，请输出中文 Markdown 故障诊断报告，禁止编造数据。",
            user_prompt,
        ).strip()
    except Exception:
        return fallback
    if not markdown:
        return fallback
    markdown = _strip_removed_report_sections(markdown)
    markdown = _append_missing_evidence_section(
        markdown=markdown,
        top_sql=top_sql,
        metrics=metrics,
    )
    return fallback.model_copy(update={"markdown": markdown})


def _fallback_report(
    *,
    task_id: str,
    profile: FaultDiagnosisProfile,
    top_sql: TopSqlEvidence,
    metrics: MetricEvidence,
    adjudication: str,
) -> FaultDiagnosisReport:
    summary = _report_summary(top_sql=top_sql, metrics=metrics)
    root_cause = adjudication
    recommendations = _recommendations(top_sql=top_sql, metrics=metrics)
    markdown = "\n".join(
        [
            "### 一、问题简述",
            (
                f"{profile.start_time} 到 {profile.end_time}，"
                f"{profile.system_name or '未知系统'}"
                f"（管理IP：{profile.management_ip or profile.primary_ip or '未识别IP'}，"
                f"业务IP：{profile.business_ip or '未匹配'}）发生数据库告警或异常："
                f"{profile.input_text}"
            ),
            "",
            "【受影响业务系统】"
            + (profile.system_name or "未从输入中识别到，请补充系统名称"),
            "",
            "### 二、影响概述",
            f"【故障影响时间】{profile.start_time} 到 {profile.end_time}",
            f"【风险评估】{summary}",
            "",
            "### 三、问题原因",
            "【原因分类】数据库性能 / 资源负载 / SQL 执行",
            f"【原因概述】{root_cause}",
            "",
            "### 四、问题分析及优化建议",
            "【监控发现】",
            _metric_markdown(metrics),
            "",
            "【TopSQL发现】",
            _top_sql_markdown(top_sql),
        ]
    )
    return FaultDiagnosisReport(
        task_id=task_id,
        summary=summary,
        markdown=markdown,
        root_cause=root_cause,
        recommendations=recommendations,
        profile=profile,
        top_sql=top_sql,
        metrics=metrics,
    )


def _strip_removed_report_sections(markdown: str) -> str:
    removed_titles = (
        "相关 SQL 及初步优化建议",
        "相关SQL及初步优化建议",
        "附录：关键数据摘要",
        "附录: 关键数据摘要",
    )
    lines = markdown.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        heading = re.match(r"^#{1,6}\s*(?P<title>.+?)\s*$", line.strip())
        if heading:
            title = heading.group("title")
            skipping = any(title.startswith(removed) for removed in removed_titles)
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _append_missing_evidence_section(
    *,
    markdown: str,
    top_sql: TopSqlEvidence,
    metrics: MetricEvidence,
) -> str:
    lines: list[str] = []
    if top_sql.status != "success":
        lines.append(f"- TopSQL 获取失败：{top_sql.error_message or 'unknown'}")
    lines.extend(f"- 监控指标获取不完整：{item}" for item in metrics.missing_metrics)
    if not lines:
        return markdown
    marker = "### 证据采集缺口"
    if marker in markdown:
        return markdown
    return "\n\n".join([markdown.rstrip(), marker, "\n".join(lines)])


def _report_summary(*, top_sql: TopSqlEvidence, metrics: MetricEvidence) -> str:
    if top_sql.status == "success" and metrics.status == "success":
        if metrics.missing_metrics:
            return "已获取 TopSQL 和部分监控指标，部分指标缺失。"
        return "已获取 TopSQL 和监控指标，存在数据库性能风险。"
    if top_sql.status == "success":
        return "已获取 TopSQL，缺少监控指标。"
    if metrics.status == "success":
        top_sql_error = top_sql.error_message or "unknown"
        if metrics.missing_metrics:
            return f"TopSQL 获取失败，部分监控指标缺失。TopSQL错误：{top_sql_error}"
        return f"TopSQL 获取失败，已获取监控指标。错误：{top_sql_error}"
    return "证据不足，需补齐 TopSQL 和监控指标。"


def _metric_markdown(metrics: MetricEvidence) -> str:
    if metrics.status != "success" or not metrics.metrics:
        return f"未获取到有效监控指标。错误：{metrics.error_message or 'unknown'}"
    rows = ["| 指标 | IP | 峰值 | 单位 |", "| --- | --- | ---: | --- |"]
    for series in metrics.metrics:
        peak = max((point.value for point in series.values), default=0.0)
        rows.append(
            f"| {series.metric_name} | {series.ip} | {peak:g} | {series.unit or ''} |"
        )
    rows.append("")
    rows.append(metrics.summary or "已获取监控指标，请结合趋势继续分析。")
    if metrics.missing_metrics:
        rows.append("")
        rows.append("未获取到的监控指标：")
        rows.extend(f"- {item}" for item in metrics.missing_metrics)
    return "\n".join(rows)


def _top_sql_markdown(top_sql: TopSqlEvidence) -> str:
    if top_sql.status != "success" or not top_sql.rows:
        return f"未获取到有效 TopSQL。错误：{top_sql.error_message or 'unknown'}"
    rows = [
        "| 数据库 | 执行次数 | 平均执行时间(s) | 总执行时间(s) | SQL摘要 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    shown_count = min(len(top_sql.rows), 5)
    for record in top_sql.rows[:shown_count]:
        sql = _normalize_sql_text(record.sql_text)
        rows.append(
            "| "
            f"{record.database or ''} | "
            f"{record.execution_count or 0} | "
            f"{record.avg_execution_seconds or 0:g} | "
            f"{record.total_execution_seconds or 0:g} | "
            f"{_inline_code(sql)} |"
        )
    rows.append("")
    rows.append(f"TopSQL 分析：共获取 {len(top_sql.rows)} 条，展示前 {shown_count} 条。")
    return "\n".join(rows)


def _normalize_sql_text(sql: str) -> str:
    return " ".join(sql.replace("\n", " ").split())


def _inline_code(value: str) -> str:
    fence = "`"
    while fence in value:
        fence += "`"
    return f"{fence}{value}{fence}"


def _exposed_problem(*, top_sql: TopSqlEvidence, metrics: MetricEvidence) -> str:
    problems: list[str] = []
    if metrics.status == "success":
        problems.append("监控指标存在异常波动，需要完善阈值、趋势和关联告警。")
        if metrics.missing_metrics:
            problems.append("部分监控指标未获取到，需要检查 PromQL、标签映射和数据源可用性。")
    if top_sql.status == "success":
        problems.append("故障窗口存在长时间运行 SQL，需要建立 TopSQL 自动巡检和 SQL 治理闭环。")
    else:
        problems.append(
            f"TopSQL 获取失败：{top_sql.error_message or 'unknown'}，影响 SQL 侧根因判断。"
        )
    if not problems:
        problems.append("当前缺少关键证据采集能力，需先打通 TopSQL 和监控指标数据源。")
    return "\n".join(f"- {problem}" for problem in problems)


def _recommendations(*, top_sql: TopSqlEvidence, metrics: MetricEvidence) -> list[str]:
    recommendations = [
        "保留本次故障窗口的监控指标、TopSQL 和慢日志，作为后续 RCA 复盘证据。",
    ]
    if top_sql.status == "success":
        recommendations.append("对 TopSQL 执行 EXPLAIN，结合表结构进一步做 SQL 改写和索引评审。")
    else:
        recommendations.append(
            "补齐 TopSQL 采集失败原因，检查 performance_schema 权限、连接账号、网络和故障窗口参数。"
        )
    if metrics.status == "success":
        recommendations.append("将 CPU、活跃线程数、连接数与 TopSQL 时间线对齐，确认因果关系。")
        if metrics.missing_metrics:
            recommendations.append(
                "补齐未获取到的监控指标，优先核对 PromQL 标签、管理 IP 到业务 IP 映射和 MCP/HTTP 数据源。"
            )
    else:
        recommendations.append("补齐 Prometheus/MCP 指标查询配置，至少覆盖 CPU、连接数、活跃线程数。")
    return recommendations


def _time_window(input_text: str, now: datetime) -> tuple[str, str]:
    alert_time = _extract_alert_time(input_text)
    if alert_time is not None:
        return (
            _format_time(alert_time - timedelta(minutes=30)),
            _format_time(alert_time),
        )
    if re.search(r"近\s*1\s*小时|最近\s*1\s*小时", input_text):
        start = now - timedelta(hours=1)
        return _format_time(start), _format_time(now)
    return _format_time(now - timedelta(hours=1)), _format_time(now)


def _extract_alert_time(text: str) -> datetime | None:
    patterns = [
        r"(?:告警时间|故障时间|发生时间|时间)\s*[:：]\s*(?P<value>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)",
        r"\b(?P<value>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parsed = _parse_datetime_text(match.group("value"))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime_text(value: str) -> datetime | None:
    normalized = value.strip().replace("/", "-")
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _extract_first_ip(text: str) -> str | None:
    tagged_patterns = [
        r"(?:管理IP|管理\s*IP|mgmt_ip|mgmt\s*ip|ip|IP)\s*[:：]\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})",
        r"实例\s*[:：]\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})(?:\|mysql_server_\d+)?",
    ]
    for pattern in tagged_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("ip")
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    return match.group(0) if match else None


def _resolve_cmdb_record(cmdb_resolver: CmdbResolver | None, management_ip: str | None):
    if cmdb_resolver is None or not management_ip:
        return None
    try:
        return cmdb_resolver.resolve_by_management_ip(management_ip)
    except Exception:
        return None


def _cmdb_value(record: object, field_name: str) -> str | None:
    if record is None:
        return None
    if isinstance(record, dict):
        value = record.get(field_name)
    else:
        value = getattr(record, field_name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _merge_profile_cmdb_fields(
    profile: FaultDiagnosisProfile,
    fallback: FaultDiagnosisProfile,
) -> FaultDiagnosisProfile:
    management_ip = profile.management_ip or fallback.management_ip or profile.primary_ip
    business_ip = profile.business_ip or fallback.business_ip
    system_name = profile.system_name or fallback.system_name
    alert_time = profile.alert_time or fallback.alert_time
    missing_fields = list(dict.fromkeys([*profile.missing_fields, *fallback.missing_fields]))
    if management_ip and business_ip and "business_ip" in missing_fields:
        missing_fields.remove("business_ip")
    return profile.model_copy(
        update={
            "management_ip": management_ip,
            "business_ip": business_ip,
            "primary_ip": profile.primary_ip or management_ip,
            "alert_time": alert_time,
            "system_name": system_name,
            "missing_fields": missing_fields,
        }
    )


def _extract_system_name(text: str) -> str | None:
    match = re.search(r"【系统\s*[:：]\s*(?P<name>[^】]+)】", text)
    if match:
        return match.group("name").strip()
    match = re.search(r"系统名称\s*[:：]\s*(?P<name>[^，,\n。]+)", text)
    if match:
        return match.group("name").strip()
    match = re.search(r"(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{2,30}系统)", text)
    return match.group("name").strip() if match else None


def _query_background(
    *,
    input_text: str,
    system_name: str | None,
    primary_ip: str | None,
    start_time: str,
    end_time: str,
) -> str:
    target = system_name or primary_ip or "未知系统"
    return f"{target} 在 {start_time} 到 {end_time} 的数据库故障诊断。原始输入：{input_text}"


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")
