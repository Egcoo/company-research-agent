"""Extract dated labels from fetched source text without rewriting metadata."""

import html
import re
from datetime import date

_MONTHS = "january february march april may june july august september october november december".split()
_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})"
_LABELS = {
    "published_at": r"(?:press\s+release|published(?:\s+on)?|publication\s+date|发布日期|发布时间)",
    "filing_date": r"(?:filing\s+date|filed(?:\s+on)?|申报日|申报日期)",
    "event_date": r"(?:event\s+date|date\s+of\s+(?:the\s+)?event|事件日|事件日期)",
}
_QUARTER = rf"\b(?:quarter(?:ly\s+period)?|(?:three|3)[\s-]+months)\s+ended\s+(?P<date>{_DATE})\b"


def _parse_date(value: str) -> date | None:
    """Parse only complete calendar dates with an explicit year."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    chinese = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if chinese:
        year, month, day = map(int, chinese.groups())
    else:
        english = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", value)
        if not english:
            return None
        name, day, year = english.groups()
        month = next(
            (
                index
                for index, item in enumerate(_MONTHS, 1)
                if name.lower() in {item, item[:3]}
            ),
            0,
        )
        year, day = int(year), int(day)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def quarter_ends(content: str) -> dict[date, str]:
    """Find explicit quarter endings, retaining their dated source passages."""
    found = {}
    for match in re.finditer(_QUARTER, html.unescape(content), re.IGNORECASE):
        parsed = _parse_date(match["date"])
        if parsed is not None:
            found.setdefault(parsed, match[0])
    return found


def _body_dates(source, kind: str) -> list[dict]:
    found = []
    for snippet in source.snippets:
        content = html.unescape(snippet.content)
        if kind == "report_date":
            dates = quarter_ends(snippet.content).items()
        else:
            # ponytail: require a dated label; unlabeled datelines need a later parser.
            pattern = rf"^\s*{_LABELS[kind]}\s*[:：]?\s*(?P<date>{_DATE})\b"
            dates = (
                (_parse_date(match["date"]), match[0].strip())
                for match in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE)
            )
        for parsed, passage in dates:
            if parsed is not None:
                found.append(
                    {
                        "date": parsed.isoformat(),
                        "snippet_id": snippet.snippet_id,
                        "text": passage,
                    }
                )
    return found


def source_dates(source, as_of: date | None = None) -> dict:
    """Resolve labeled dates and provenance, never infer historical availability.

    Return JSON-serializable support records without changing source metadata.
    Body text is unescaped only for matching; original snippets remain authoritative.
    SEC sources use published_at for the filing date in the existing fetch contract.
    A dated page does not verify that this fetched version existed on that date.
    """
    metadata = {
        "published_at": source.published_at if source.source_type != "sec" else None,
        "filing_date": source.published_at if source.source_type == "sec" else None,
        "report_date": source.report_date,
        "event_date": None,
    }
    result = {}
    for kind, recorded_date in metadata.items():
        candidates = _body_dates(source, kind)
        body_values = {item["date"] for item in candidates}
        support = {
            "date": None,
            "basis": "unverified",
            "metadata_missing": recorded_date is None,
            "after_cutoff": None,
            "candidates": candidates,
        }
        if recorded_date is not None:
            # Comparative quarters can coexist with the filing's primary period.
            conflict = kind != "report_date" and body_values - {
                recorded_date.isoformat()
            }
            if conflict:
                support["basis"] = "conflict"
                support["metadata_date"] = recorded_date.isoformat()
            else:
                support.update(date=recorded_date.isoformat(), basis="metadata")
        elif len(body_values) == 1:
            support.update(**candidates[0], basis="source_excerpt")
        elif body_values:
            support["basis"] = "ambiguous"
        if support["date"] and as_of is not None:
            support["after_cutoff"] = date.fromisoformat(support["date"]) > as_of
        result[kind] = support
    return result
