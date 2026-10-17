"""Plan and conduct stock research with injected tools and runtime callbacks."""

import json
import time

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from research_agent.agent import _text
from research_agent.agent.instructions import FUND_RESEARCH, STOCK_RESEARCH
from research_agent.config import ResearchError


def run_research(
    *,
    model,
    methodology,
    query,
    ticker,
    as_of,
    source_tools,
    budget,
    research_until,
    model_options,
    record_response,
    save_note,
    emit_event,
    target_type="stock",
    fund_type=None,
    share_class=None,
):
    """Let one tool-using model plan, read, calculate and keep free-form notes."""
    initial = (
        source_tools.discover_fund_sources()
        if target_type == "fund"
        else source_tools.discover_sec_sources()
    )
    if target_type == "fund" and not source_tools.fund_data.get("identity_verified"):
        raise ResearchError(
            "fund_identity_unverified",
            "Fund identity and NAVs must be verified before model research.",
        )
    tools = {tool.name: tool for tool in source_tools.tools()}
    system = methodology + (FUND_RESEARCH if target_type == "fund" else STOCK_RESEARCH)
    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=json.dumps(
                {
                    "question": query,
                    "ticker": ticker,
                    "as_of": str(as_of),
                    "initial_sec_discovery": initial,
                }
                if target_type == "stock"
                else {
                    "question": query,
                    "fund_code": ticker,
                    "as_of": str(as_of),
                    "fund_type": fund_type,
                    "share_class": share_class,
                    "cutoff_weekday": as_of.strftime("%A"),
                    "initial_fund_materials": initial,
                },
                ensure_ascii=False,
            )
        ),
    ]
    empty = 0
    while budget.remaining()["model"] > 2 and time.monotonic() < research_until:
        closing = (
            research_until - time.monotonic() <= 60
            or budget.remaining()["model"] <= 4
            or budget.remaining()["request"] <= 0
        )
        available = {
            name: item
            for name, item in tools.items()
            if not closing or name in {"save_notes", "calculate"}
        }
        control = HumanMessage(
            content=(
                (
                    "Research acquisition is closed. Save fund identity, dated NAV/risk/tracking calculations, index and target-ETF exposure, fees, alternatives and gaps already obtained. Finish with Markdown notes; do not retrieve more sources."
                    if target_type == "fund"
                    else "Research acquisition is closed. Save the substantive research, financial tables, forecast assumptions, valuation calculations and gaps already obtained. Finish with Markdown notes; do not retrieve more sources."
                )
                if closing
                else f"Continue within remaining limits: {budget.remaining()}. Preserve findings in Markdown notes as you go."
            )
        )
        response = model.bind_tools(
            list(available.values()), **model_options("research")
        ).invoke([*messages, control])
        record_response(response, "research")
        text = _text(response)
        save_note(text)
        messages.extend([control, response])
        calls = response.tool_calls
        if not calls:
            if text.strip() and source_tools.sources:
                break
            empty += 1
            if empty >= 2:
                emit_event({"type": "research_empty_response"})
                break
            messages.append(
                HumanMessage(
                    content="A plan alone is not research. Read the discovered disclosure URLs and other necessary sources, then save substantive Markdown analysis. An empty reply does not deliver research."
                )
            )
            continue
        for call in calls:
            if call["name"] not in available:
                result = {"error": "tool_unavailable", "gap": True}
            else:
                try:
                    result = available[call["name"]].invoke(call["args"])
                except Exception as error:
                    result = {
                        "error": "tool_input_invalid",
                        "kind": type(error).__name__,
                        "gap": True,
                    }
                    emit_event(
                        {
                            "type": "tool_error",
                            "tool": call["name"],
                            "code": "tool_input_invalid",
                        }
                    )
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
