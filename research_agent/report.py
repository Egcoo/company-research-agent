"""Render provenance and runtime gaps without model-made citation bookkeeping."""

import hashlib
import html
import json
import re
from pathlib import Path

from research_agent.validation import requirement_coverage, support_evidence


def _text(value):
    return _body(str(value)).replace("|", "\\|").replace("\n", " ")


def _source_link(source):
    title = _text(source.title).replace("[", "\\[").replace("]", "\\]")
    return f"[{title}](<{source.url}>)"


def _body(text):
    """Escape HTML and link syntax while keeping other Markdown formatting."""
    text = re.sub(r"\]\([^)]*\)", "]（引用由程序登记）", text)
    text = re.sub(
        r"\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"']+",
        "（引用由程序登记）",
        text,
        flags=re.IGNORECASE,
    )
    # Bracket entities display normally but cannot start links, images or definitions.
    return html.escape(text, quote=False).replace("[", "&#91;").replace("]", "&#93;")


def _section_body(text, number, heading):
    """Let runtime own primary headings without discarding model subtopics."""
    text = re.sub(
        rf"(?m)^\s{{0,3}}#{{1,2}}\s+(?:{number}[.)]\s*)?{re.escape(heading)}\s*#*\s*$",
        "",
        text,
    )
    text = re.sub(r"(?m)^(\s{0,3})#{1,2}(?=\s)", r"\1###", text)
    return _body(text.strip())


def _supports(run, refs):
    """Show compact nearby provenance and canonical numeric context."""
    groups = {}
    metric_evidence = {
        run.support_index[ref]["record"]["evidence_id"]
        for ref in refs
        if run.support_index[ref]["kind"] == "metrics"
    }
    for ref in refs:
        entry = run.support_index[ref]
        kind, record = entry["kind"], entry["record"]
        if kind == "evidence" and record["evidence_id"] in metric_evidence:
            continue
        evidence = support_evidence(entry, run.ledger)
        links = list(
            dict.fromkeys(
                _source_link(run.ledger.sources[item.source_id]) for item in evidence
            )
        )
        if kind == "metrics":
            label = f"{record['metric']}：{record['value']} {record['unit']} / {record['currency'] or '不适用'}；{record['reporting_period'] or record['effective_date']}"
        elif kind == "calculations":
            label = f"计算（{record['operation']}）：输入 {', '.join(record['values'])} → {record['result']}；算式复现不等于输入已核实"
        else:
            label = record["topic"]
        states = {item.status for item in evidence}
        if "unresolved" in states:
            label += "（未核实）"
        elif "source_opinion" in states:
            label += "（含来源观点）"
        labels = groups.setdefault(tuple(links), [])
        if _text(label) not in labels:
            labels.append(_text(label))
    return "；".join(
        "；".join(labels) + (" " + "、".join(links) if links else "")
        for links, labels in groups.items()
    )


def _source_summary(run, *, include_metrics=True):
    """Show decision facts and source links without copying source documents."""
    labels = {
        "verified": "已核实",
        "source_opinion": "来源观点",
        "unresolved": "未核实",
    }
    lines = [
        "### 信息口径与证据表",
        "",
        "| 关键事实 | 规范值与口径 | 日期或期间 | 来源 | 状态 |",
        "| --- | --- | --- | --- | --- |",
    ]
    metric_evidence = set()
    for metric in run.ledger.metrics.values():
        metric_evidence.add(metric.evidence_id)
        if not include_metrics:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _text(metric.metric),
                    _text(
                        f"{metric.value} {metric.unit} / {metric.currency or '不适用'}；{metric.definition}"
                    ),
                    _text(metric.reporting_period or metric.effective_date),
                    _source_link(run.ledger.sources[metric.source_id]),
                    labels[metric.status],
                ]
            )
            + " |"
        )
    for evidence in run.ledger.evidence.values():
        if evidence.evidence_id in metric_evidence:
            continue
        statement = evidence.statement
        if len(statement) > 240:
            statement = statement[:240] + "…（详见来源链接）"
        lines.append(
            "| "
            + " | ".join(
                [
                    _text(evidence.topic),
                    _text(statement),
                    _text(
                        evidence.reporting_period or evidence.effective_date or "未建立"
                    ),
                    _source_link(run.ledger.sources[evidence.source_id]),
                    labels[evidence.status],
                ]
            )
            + " |"
        )
    lines.extend(["", "### 主要来源", ""])
    for source in run.ledger.sources.values():
        lines.append(
            "- "
            + _source_link(source)
            + "；类型："
            + _text(source.source_type)
            + "；披露日期："
            + _text(source.published_at or "未核实")
            + "。"
        )
    return [*lines, ""]


def render_report(run, bundle):
    """Keep source statements, research inference and judgment visibly separate."""
    headings = bundle.report_headings or [bundle.focused_heading]
    focused = not bundle.report_headings
    attachments = {
        name: headings.index(heading)
        for name, heading in bundle.report_attachments.items()
        if heading in headings
    }
    if not bundle.report_headings:
        attachments = {"judgment": 0, "metrics": 0, "traceability": 0}
    metrics_position = attachments.get("metrics", len(headings) - 1)
    traceability_position = attachments.get("traceability", len(headings) - 1)
    texts = run.report_draft.sections
    if any(not text.strip() for text in texts):
        run.checks.append({"code": "report_empty_section"})
        run.status = "partial"
    if len(texts) != len(headings):
        run.checks.append(
            {
                "code": "report_section_count",
                "expected": len(headings),
                "actual": len(texts),
            }
        )
        run.status = "partial"
    if getattr(run, "execution_coverage", None):
        run.execution_coverage["report_structure_matches"] = len(texts) == len(
            headings
        ) and all(text.strip() for text in texts)
    lines = [
        f"# {_text(run.target.name)}研究报告",
        "",
        f"信息截止：{run.as_of} · 运行状态：{run.status}",
        "",
    ]
    requirements = getattr(run, "execution_coverage", {}).get("requirements", [])
    incomplete = [item for item in requirements if item["status"] != "satisfied"]
    if incomplete and focused:
        lines.extend(["**尚未完成的研究：**", ""])
        lines.extend(
            "- "
            + _text("、".join(item["report_headings"]) + "：" + "；".join(item["gaps"]))
            for item in incomplete
        )
        lines.append("")
    for index, heading in enumerate(headings):
        lines.extend(
            [
                f"## {index + 1}. {heading}",
                "",
            ]
        )
        if focused:
            lines.extend(["**核心答复：** " + _text(run.judgment.conclusion), ""])
            if run.report_draft.judgment_support_refs:
                lines.extend(
                    [
                        "**依据：** "
                        + _supports(run, run.report_draft.judgment_support_refs),
                        "",
                    ]
                )
        if run.report_draft.blocks and index < len(run.report_draft.blocks):
            for block in run.report_draft.blocks[index]:
                lines.extend([_section_body(block.text, index + 1, heading), ""])
                if block.support_refs:
                    lines.extend(
                        ["**依据：** " + _supports(run, block.support_refs), ""]
                    )
                if block.assumptions:
                    lines.extend(
                        [
                            "**假设：** "
                            + "；".join(_text(item) for item in block.assumptions),
                            "",
                        ]
                    )
                if block.gap:
                    lines.extend(["**信息缺口：** " + _text(block.gap), ""])
        else:
            lines.extend(
                [
                    _section_body(texts[index], index + 1, heading)
                    if index < len(texts) and texts[index].strip()
                    else "本节证据不足，当前无法形成完整判断。",
                    "",
                ]
            )
        if index == attachments.get("judgment", 0) and not focused:
            lines.extend(
                ["**最终判断（基于研究发现）：** " + _text(run.judgment.conclusion), ""]
            )
            if run.report_draft.judgment_support_refs:
                lines.extend(
                    [
                        "**判断依据：** "
                        + _supports(run, run.report_draft.judgment_support_refs),
                        "",
                    ]
                )
        if index == attachments.get("judgment", 0):
            if run.judgment.conditions:
                lines.extend(
                    [
                        "**判断适用条件：** "
                        + "；".join(_text(item) for item in run.judgment.conditions),
                        "",
                    ]
                )
            if run.judgment.rationale:
                lines.extend(
                    [
                        "**判断理由：** "
                        + "；".join(_text(item) for item in run.judgment.rationale),
                        "",
                    ]
                )
            if run.judgment.risks:
                lines.extend(
                    [
                        "**判断风险：** "
                        + "；".join(_text(item) for item in run.judgment.risks),
                        "",
                    ]
                )
        if (
            run.ledger.metrics
            and index == metrics_position
            and metrics_position != traceability_position
        ):
            lines.extend(
                [
                    "### 本次研究使用的指标",
                    "",
                    "| 指标 | 数值 | 单位 / 币种 | 时间口径 | 定义 | 状态 | 来源 |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for metric in run.ledger.metrics.values():
                source = run.ledger.sources[metric.source_id]
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _text(metric.metric),
                            _text(metric.value),
                            _text(f"{metric.unit} / {metric.currency or '不适用'}"),
                            _text(metric.reporting_period or metric.effective_date),
                            _text(metric.definition),
                            _text(metric.status),
                            _source_link(source),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if index == attachments.get("traceability", len(headings) - 1):
            if len(texts) > len(headings):
                lines.extend(
                    [
                        "### 补充研究材料",
                        "",
                        "\n\n".join(_body(text) for text in texts[len(headings) :]),
                        "",
                    ]
                )
            lines.extend(
                _source_summary(
                    run, include_metrics=metrics_position == traceability_position
                )
            )
            lines.extend(["", "### 未核实或证据不足的信息", ""])
            if incomplete and not focused:
                lines.extend(["**尚未完成的研究：**", ""])
                lines.extend(
                    "- "
                    + _text(
                        "、".join(item["report_headings"])
                        + "："
                        + "；".join(item["gaps"])
                    )
                    for item in incomplete
                )
                lines.append("")
            coverage = getattr(run, "execution_coverage", {})
            if coverage:
                lines.extend(
                    [
                        "执行覆盖记录本次适用要求、任务与支持材料；声明性覆盖与引用检查不等于完整的事实或投资质量核验。",
                        "",
                    ]
                )
                if coverage.get("unplanned_responsibilities") and not getattr(
                    run, "execution_contract", None
                ):
                    lines.extend(
                        [
                            "本次未安排的已注册职责："
                            + "、".join(coverage["unplanned_responsibilities"])
                            + "。适用性由问题范围和 Skill 决定；未安排不代表该主题已经研究。",
                            "",
                        ]
                    )
            gaps = list(
                dict.fromkeys(
                    [
                        text
                        for key, values in run.task_gaps.items()
                        for text in values
                        if text not in getattr(run, "runtime_warnings", {}).get(key, [])
                    ]
                    + [text for result in run.results.values() for text in result.gaps]
                    + run.judgment.limitations
                    + [gap for item in incomplete for gap in item["gaps"]]
                    + [
                        block.gap
                        for blocks in run.report_draft.blocks
                        for block in blocks
                        if block.gap
                    ]
                )
            )
            lines.extend(
                [f"- {_text(gap)}" for gap in gaps]
                or ["- 本次已登记任务没有额外运行缺口。"]
            )
            warnings = getattr(run, "runtime_warnings", {})
            if warnings:
                lines.extend(["", "### 运行提示（必要材料已取得，原错误记录保留）", ""])
                lines.extend(
                    "- " + _text(message)
                    for values in warnings.values()
                    for message in values
                )
            if run.checks:
                check_labels = {
                    "legacy_report_support_unavailable": "旧报告未保留逐段依据。",
                    "judgment_support_missing": "最终判断缺少可追溯依据。",
                    "decisive_support_missing": "部分关键结论缺少可追溯依据。",
                    "unresolved_report_support": "部分正文所用依据尚未核实。",
                    "unresolved_judgment_support": "最终判断所用依据尚未核实。",
                    "limited_report_support": "部分正文依据仍有适用限制。",
                    "limited_judgment_support": "最终判断依据仍有适用限制。",
                    "declared_report_gap": "正文已说明尚未完成的研究内容。",
                    "calculation_inputs_untraced": "部分计算输入无法追溯。",
                    "requirement_report_support_missing": "部分必要研究内容缺少对应的正文依据。",
                    "report_empty_section": "部分必要报告内容为空。",
                    "report_section_count": "报告未完整交付所要求的内容。",
                }
                lines.extend(
                    "- " + label
                    for label in dict.fromkeys(
                        check_labels.get(
                            check["code"], "部分交付检查未通过，详见 checks.json。"
                        )
                        for check in run.checks
                    )
                )
            lines.extend(
                [
                    "",
                    "已登记链接和结构检查不等于对任意分析文字的语义正确性保证。"
                    "本报告区分来源陈述、来源观点、研究推断和最终判断。",
                    "",
                ]
            )
    return "\n".join(lines)


def export_run(run, result, output_dir):
    """Save registered research and source metadata, without source document copies."""
    destination = Path(output_dir) / run.run_id
    destination.mkdir(parents=True, exist_ok=False)
    ledger = run.ledger
    if getattr(run, "execution_contract", None) and not run.execution_coverage:
        run.execution_coverage = {
            "execution_contract": run.execution_contract,
            "requirements": requirement_coverage(run) if ledger else [],
            "semantics_verified": False,
        }
    artifacts = {
        "result.json": result,
        "inputs.json": {
            "query": run.query,
            "as_of": run.as_of.isoformat(),
            "ticker": run.ticker,
            "scope": run.scope,
            "target_options": run.target_options,
        },
        "target.json": run.target.model_dump(mode="json") if run.target else None,
        "skill_manifest.json": run.skill_manifest,
        "plan.json": [task.model_dump(mode="json") for task in run.tasks],
        "tasks.json": [item.model_dump(mode="json") for item in run.results.values()],
        "sources.json": [
            {
                **item.model_dump(mode="json", exclude={"text", "locations"}),
                "content_hash": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            }
            for item in ledger.sources.values()
        ]
        if ledger
        else [],
        "evidence.json": [
            item.model_dump(mode="json") for item in ledger.evidence.values()
        ]
        if ledger
        else [],
        "findings.json": [
            item.model_dump(mode="json") for item in ledger.findings.values()
        ]
        if ledger
        else [],
        "metrics.json": [
            item.model_dump(mode="json") for item in ledger.metrics.values()
        ]
        if ledger
        else [],
        "calculations.json": [
            item.model_dump(mode="json") for item in ledger.calculations.values()
        ]
        if ledger
        else [],
        "judgment.json": run.judgment.model_dump(mode="json") if run.judgment else None,
        "checks.json": run.checks,
        "events.json": run.events,
        "model_field_calls.json": run.model_field_calls,
        "execution_coverage.json": getattr(run, "execution_coverage", {}),
        "requirement_results.json": getattr(run, "requirement_results", {}),
        "support_index.json": getattr(run, "support_index", {}),
    }
    if run.report_draft:
        artifacts["report_draft.json"] = run.report_draft.model_dump(mode="json")
    if getattr(run, "schema_diagnostics", None):
        artifacts["schema_diagnostics.json"] = run.schema_diagnostics
    for filename, content in artifacts.items():
        (destination / filename).write_text(
            json.dumps(content, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    if run.results:
        task_dir = destination / "tasks"
        task_dir.mkdir()
        for identifier, item in run.results.items():
            (task_dir / f"{identifier}.md").write_text(
                item.narrative + "\n", encoding="utf-8"
            )
    if run.report_markdown:
        (destination / "report.md").write_text(run.report_markdown, encoding="utf-8")
    return destination
