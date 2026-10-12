"""Verify provider-specific disclosure coverage without live requests."""

from datetime import date
from types import SimpleNamespace

import pytest

from research_agent.config import ResearchError
from research_agent.tools.research import discover_recent_sec_sources

CUTOFF = date(2026, 9, 30)


def sec_payload(forms, *, cik="0001737806"):
    columns = {
        "accessionNumber": [],
        "filingDate": [],
        "reportDate": [],
        "form": [],
        "primaryDocument": [],
        "items": [],
    }
    for index, form in enumerate(forms, 1):
        values = (
            f"{cik}-26-{index:06d}",
            "2026-08-01",
            "2026-06-30",
            form,
            f"disclosure-{index}.htm",
            "2.02" if form == "8-K" else "",
        )
        for key, value in zip(columns, values, strict=True):
            columns[key].append(value)
    return {
        "cik": cik,
        "filings": {"recent": columns, "files": [{"name": "older.json"}]},
    }


@pytest.mark.parametrize(
    "ticker,cik,forms",
    [
        ("AAPL", "0000320193", ["10-Q", "10-K", "8-K"]),
        ("NVDA", "0001045810", ["10-Q", "10-K", "8-K"]),
        ("PDD", "0001737806", ["20-F", "6-K"]),
    ],
)
def test_disclosure_forms_depend_on_provider_records_not_company_branches(
    ticker, cik, forms
):
    requests = []
    payload = sec_payload(forms, cik=cik)
    sec = SimpleNamespace(_get_json=lambda url: requests.append(url) or payload)
    company = SimpleNamespace(cik=cik, ticker=ticker)
    result = discover_recent_sec_sources(sec, company, CUTOFF)
    assert {row["form"] for row in result["filings"]} == set(forms)
    assert result["coverage"] == "recent_only"
    assert requests == [f"https://data.sec.gov/submissions/CIK{cik}.json"]
    if "6-K" in forms:
        assert any("event rather than earnings" in gap for gap in result["gaps"])


def test_cutoff_excludes_future_filings_and_limit_keeps_latest_periodic():
    payload = sec_payload(["10-Q", "10-K", "8-K", "6-K"])
    recent = payload["filings"]["recent"]
    recent["reportDate"][1] = "2025-12-31"
    recent["filingDate"][3] = "2026-10-01"
    sec = SimpleNamespace(_get_json=lambda url: payload)
    result = discover_recent_sec_sources(
        sec, SimpleNamespace(cik="0001737806"), CUTOFF, 1
    )
    assert len(result["filings"]) == 1
    assert result["filings"][0]["form"] == "10-Q"
    assert any("limit excluded" in gap for gap in result["gaps"])


@pytest.mark.parametrize(
    "corruption", ["cik", "columns", "accession", "document", "items"]
)
def test_sec_payload_and_accession_boundaries_are_not_relaxed(corruption):
    payload = sec_payload(["20-F"])
    recent = payload["filings"]["recent"]
    if corruption == "cik":
        payload["cik"] = "0000320193"
    elif corruption == "columns":
        recent["reportDate"] = []
    elif corruption == "accession":
        recent["accessionNumber"][0] = "invented"
    elif corruption == "document":
        recent["primaryDocument"][0] = "../outside.htm"
    else:
        recent["items"][0] = None
    sec = SimpleNamespace(_get_json=lambda url: payload)
    with pytest.raises(ResearchError) as failure:
        discover_recent_sec_sources(sec, SimpleNamespace(cik="0001737806"), CUTOFF)
    assert failure.value.code in {"sec_parse_error", "unsafe_sec_url"}


def test_missing_recent_disclosures_returns_gap_without_archived_ingestion():
    payload = sec_payload([])
    calls = []
    sec = SimpleNamespace(_get_json=lambda url: calls.append(url) or payload)
    result = discover_recent_sec_sources(sec, SimpleNamespace(cik="0001737806"), CUTOFF)
    assert result["filings"] == []
    assert any("No recent periodic" in gap for gap in result["gaps"])
    assert len(calls) == 1


def test_adapter_cik_cannot_hide_a_conflicting_submission_ticker():
    payload = sec_payload(["20-F"])
    payload["tickers"] = ["OTHER"]
    sec = SimpleNamespace(_get_json=lambda url: payload)
    with pytest.raises(ResearchError) as failure:
        discover_recent_sec_sources(
            sec, SimpleNamespace(cik="0001737806", ticker="PDD"), CUTOFF
        )
    assert failure.value.code == "target_mismatch"
