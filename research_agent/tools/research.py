"""Discover recent SEC disclosures and declare pinned Skill capability IDs."""

import re
from datetime import date

from research_agent.config import ResearchError
from research_agent.tools.sec import ARCHIVES_ROOT, SUBMISSIONS_ROOT, _validate_url

_FORMS = {"10-Q", "10-K", "8-K", "20-F", "6-K"}
CAPABILITY_IDS = frozenset(
    {
        "web.search",
        "web.read",
        "math.calculate",
        "sec.resolve",
        "sec.disclosures",
        "sec.attachments",
    }
)


def discover_recent_sec_sources(sec, company, as_of: date, limit: int = 6) -> dict:
    """Select bounded recent disclosures, including foreign-issuer forms."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ResearchError(
            "invalid_limit", "SEC filing limit must be between 1 and 10."
        )
    payload = sec._get_json(f"{SUBMISSIONS_ROOT}CIK{company.cik}.json")
    try:
        if int(payload["cik"]) != int(company.cik):
            raise ValueError("CIK mismatch")
        tickers = payload.get("tickers")
        if tickers is not None and getattr(company, "ticker", None):
            if not isinstance(tickers, list) or company.ticker not in tickers:
                raise ResearchError(
                    "target_mismatch", "SEC submissions do not match the bound ticker."
                )
        recent = payload["filings"]["recent"]
        keys = (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "form",
            "primaryDocument",
        )
        columns = [recent[key] for key in keys]
        if (
            any(not isinstance(column, list) for column in columns)
            or len({len(column) for column in columns}) != 1
        ):
            raise ValueError("Unaligned columns")
        items = recent.get("items", [""] * len(columns[0]))
        if (
            not isinstance(items, list)
            or len(items) != len(columns[0])
            or any(not isinstance(item, str) for item in items)
        ):
            raise ValueError("Unaligned items")
        filings = []
        for row, item in zip(zip(*columns, strict=True), items, strict=True):
            accession, filed, period, form, document = row
            if form not in _FORMS:
                continue
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                raise ValueError("Invalid accession")
            filing_date = date.fromisoformat(filed)
            report_date = date.fromisoformat(period) if period else None
            if filing_date > as_of or (report_date is not None and report_date > as_of):
                continue
            directory = (
                f"{ARCHIVES_ROOT}{int(company.cik)}/{accession.replace('-', '')}/"
            )
            url = directory + document
            _validate_url(url, directory, directory=True)
            filings.append(
                {
                    "form": form,
                    "accession_number": accession,
                    "filing_date": filed,
                    "report_date": period or None,
                    "url": url,
                    "directory": directory,
                    "items": item,
                }
            )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ResearchError(
            "sec_parse_error", "SEC recent disclosures have an invalid structure."
        ) from None
    filings.sort(key=lambda row: row["filing_date"], reverse=True)
    periodic = sorted(
        (row for row in filings if row["form"] in {"10-Q", "10-K", "20-F"}),
        key=lambda row: (row["report_date"] or "", row["filing_date"]),
        reverse=True,
    )
    selected = periodic[:1]
    earnings = [
        row
        for row in filings
        if row["form"] == "6-K"
        or (row["form"] == "8-K" and "2.02" in re.split(r"[,;\s]+", row["items"]))
    ]
    selected.extend(earnings[:1])
    annual = next((row for row in periodic if row["form"] in {"10-K", "20-F"}), None)
    if annual is not None and annual not in selected:
        selected.append(annual)
    selected.extend(row for row in earnings[1:] if row not in selected)
    gaps = [
        "Coverage is recent-only; archived disclosures and amended-report reconciliation are not supported."
    ]
    if not periodic:
        gaps.append("No recent periodic disclosure was available by the cutoff.")
    if len(selected) > limit:
        gaps.append("The filing limit excluded supporting disclosures.")
    if any(row["form"] == "6-K" for row in selected):
        gaps.append(
            "A 6-K may describe an event rather than earnings; its reporting period requires source review."
        )
    return {"filings": selected[:limit], "coverage": "recent_only", "gaps": gaps}
