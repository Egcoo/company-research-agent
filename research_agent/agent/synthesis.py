"""Synthesize a Markdown report from saved notes and actual reading windows."""

import json
from datetime import datetime, timedelta, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from research_agent.agent import _text
from research_agent.agent.instructions import FUND_SYNTHESIS, STOCK_SYNTHESIS
from research_agent.config import ResearchError
from research_agent.runtime.reporting import (
    _source_list,
    check_report,
    insert_fund_costs,
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
    target_type="stock",
    fund_data=None,
    fund_type=None,
    share_class=None,
    report_filename="2026-10-13-GOOG.md",
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
                    for key in (
                        "title",
                        "url",
                        "published_at",
                        "text",
                        "truncated",
                        *(
                            ("retrieved_at", "effective_date", "pages")
                            if target_type == "fund"
                            else ()
                        ),
                    )
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
    if target_type == "fund":
        packet.pop("ticker")
        packet.update(
            fund_code=ticker,
            fund_type=fund_type,
            share_class=share_class,
            cutoff_weekday=as_of.strftime("%A"),
            report_date=datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
            fund_data=fund_data,
        )
    emit_event(
        {
            "type": "synthesis_input",
            "source_windows": len(windows),
            "source_characters": sum(len(item["text"]) for item in windows),
            "note_characters": len(notes),
        }
    )
    system = methodology + (
        FUND_SYNTHESIS if target_type == "fund" else STOCK_SYNTHESIS
    )
    if target_type == "fund":
        system += (
            "\nUse report_date for the generation date and as_of for the information cutoff. "
            "retrieved_at is the acquisition timestamp, not the publication or NAV date. "
            "A page fetched after cutoff does not prove its undated current values were known at cutoff."
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
        if target_type == "fund":
            report = insert_fund_costs(report, fund_data, headings)
        checks = check_report(report, headings, allowed_urls)
        emit_event({"type": "report_check", "attempt": attempt + 1, **checks})
        if not checks["errors"]:
            report = report.rstrip() + "\n\n" + _source_list(sources)
            (directory / report_filename).write_text(report, encoding="utf-8")
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
