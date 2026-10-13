"""Synthesize a Markdown report from saved notes and actual reading windows."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from research_agent.agent import _text
from research_agent.config import ResearchError
from research_agent.runtime.reporting import (
    _source_list,
    check_report,
    normalize_markdown,
)


def synthesize_report(
    *,
    model,
    methodology,
    query,
    ticker,
    as_of,
    headings,
    sources,
    allowed_urls,
    research_errors,
    budget,
    directory,
    model_options,
    record_response,
    emit_event,
):
    """Stream Markdown to disk, checking only structure and read-source citations."""
    if not sources:
        raise ResearchError(
            "sources_unavailable",
            "No source text was successfully read; research cannot be delivered.",
        )
    notes_path = directory / "notes.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    # Original reading windows back up unfinished notes; a final research DTO is unnecessary.
    windows, seen = [], set()
    for item in sources:
        key = (item["url"], item["text"])
        if key not in seen:
            seen.add(key)
            windows.append(
                {
                    key: item.get(key)
                    for key in ("title", "url", "published_at", "text", "truncated")
                }
            )
    packet = {
        "question": query,
        "ticker": ticker,
        "as_of": str(as_of),
        "notes": notes,
        "headings": headings,
        "read_sources": windows,
        "research_errors": research_errors,
    }
    emit_event(
        {
            "type": "synthesis_input",
            "source_windows": len(windows),
            "source_characters": sum(len(item["text"]) for item in windows),
            "note_characters": len(notes),
        }
    )
    system = methodology + (
        "\nRuntime delivery: Write the actual full Chinese investment research report directly as Markdown, not JSON or a tool call. "
        "Use the nine supplied core headings. Lead with a decision table and three reasons/risks, then explain business and industry, management, "
        "historical financial quality, driver forecasts, valuation and scenarios, risks and execution conditions. "
        "Keep actuals, assumptions and forecasts separate; include units, periods and the price date. Use readable tables and connected analysis. "
        "Build the analysis from supplied research and original reading windows. Do not invent missing facts or pretend omitted research was done. "
        "Notes are working drafts, not verified evidence. A source URL or a number mentioned only in notes/search leads cannot support a factual claim. "
        "Use only facts supported by read_sources text; turn unread industry or management claims into explicit gaps rather than investment reasons. "
        "Align financial columns with their original dates: prefer the latest two full years and latest interim; do not put a balance-sheet value into an earlier year. "
        "ROE uses average opening/closing equity if calculated; otherwise label a different definition or omit it with a gap. "
        "Recompute PE from the requested price date and EPS; do not copy a ratio based on another day's price. "
        "Show a transparent two-year earnings/cash-flow bridge and an absolute cash-flow valuation with a relative PE cross-check. "
        "Expose forecast tax, costs/margins, diluted shares and cash conversion, DCF cash flows/discounting/terminal value, net cash and per-share conversion. "
        "If these inputs are assumptions, label them explicitly and test sensitivity. Do not invent technical support levels from a few nearby closes. "
        "Use ordinary Markdown source links near decisive claims, selecting only URLs in read_sources. "
        "Section nine contains source priorities, important gaps and disclaimer; the application appends the read-source list. "
        "Do not write IDs, runtime internals, a plan for a future report, or claims that you saved a file. "
        "Give a substantive report of roughly 6000 to 10000 Chinese characters when the evidence supports it."
    )
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=json.dumps(packet, ensure_ascii=False, default=str)),
    ]
    for attempt in range(2):
        options = model_options("synthesis")
        draft_path = directory / (
            "report.draft.md" if attempt == 0 else "report.repair.md"
        )
        content = ""
        combined = None
        with draft_path.open("w", encoding="utf-8") as stream:
            for chunk in model.bind(**options).stream(messages):
                part = _text(chunk)
                if part:
                    content += part
                    stream.write(part)
                    stream.flush()
                combined = chunk if combined is None else combined + chunk
                budget.check()
        if combined is not None:
            record_response(combined, "synthesis")
        report = normalize_markdown(content, headings)
        checks = check_report(report, headings, allowed_urls)
        emit_event({"type": "report_check", "attempt": attempt + 1, **checks})
        if not checks["errors"]:
            report = report.rstrip() + "\n\n" + _source_list(sources)
            (directory / "2026-10-13-GOOG.md").write_text(report, encoding="utf-8")
            return checks
        if attempt == 0:
            messages.append(
                HumanMessage(
                    content="Repair this Markdown using the same material, with no new claims or URLs. Preserve valid analysis and explain unavailable facts as gaps. "
                    + json.dumps(checks, ensure_ascii=False)
                    + "\n\nCurrent draft:\n"
                    + report
                )
            )
    return checks
