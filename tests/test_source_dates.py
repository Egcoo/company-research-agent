"""Test source-date provenance using synthetic fetched text only."""

import html
import json
from datetime import date
from types import SimpleNamespace

from research_agent.tools.contracts import TextSnippet
from research_agent.tools.source_dates import quarter_ends, source_dates


def source(content, **updates):
    return SimpleNamespace(
        snippets=[TextSnippet(snippet_id="body-1", locator="text:0", content=content)],
        **{
            "source_type": "web",
            "published_at": None,
            "report_date": None,
            **updates,
        },
    )


def test_body_date_is_registered_with_provenance_and_original_metadata_retained():
    original = source(
        "Navigation\nPRESS RELEASE\nJuly&nbsp;28, 2026\nResults for the fiscal third "
        "quarter ended June 25, 2026. A dividend is payable August 18, 2026."
    )
    dates = source_dates(original, date(2026, 10, 1))
    assert dates["published_at"]["date"] == "2026-07-28"
    assert dates["published_at"]["basis"] == "source_excerpt"
    assert dates["published_at"]["metadata_missing"]
    assert dates["published_at"]["snippet_id"] == "body-1"
    assert dates["published_at"]["text"] in html.unescape(original.snippets[0].content)
    assert dates["report_date"]["date"] == "2026-06-25"
    assert dates["filing_date"]["date"] is None
    assert original.published_at is None and original.report_date is None
    json.dumps(dates)


def test_missing_publication_date_stays_unknown_despite_event_period_and_url():
    dates = source_dates(
        source(
            "Event date: July 28, 2026\nQuarter ended June 25, 2026.\n"
            "The company announced its results July 28, 2026."
        )
    )
    assert dates["published_at"]["date"] is None
    assert dates["published_at"]["basis"] == "unverified"
    assert dates["event_date"]["date"] == "2026-07-28"
    assert dates["report_date"]["date"] == "2026-06-25"


def test_distinct_labels_and_sec_metadata_keep_date_roles_separate():
    dates = source_dates(
        source(
            "Published: July 28, 2026\nFiling date: July 29, 2026\n"
            "Event date: July 27, 2026\nFor the quarterly period ended June 25, 2026",
            source_type="sec",
            published_at=date(2026, 7, 29),
            report_date=date(2026, 6, 25),
        )
    )
    assert dates["published_at"]["date"] == "2026-07-28"
    assert dates["filing_date"]["date"] == "2026-07-29"
    assert dates["filing_date"]["basis"] == "metadata"
    assert dates["event_date"]["date"] == "2026-07-27"
    assert dates["report_date"]["date"] == "2026-06-25"


def test_ambiguous_dates_and_conflicting_metadata_are_not_silently_resolved():
    text = "Published: July 28, 2026\nPublished: July 29, 2026"
    ambiguous = source_dates(source(text))["published_at"]
    assert ambiguous["date"] is None and ambiguous["basis"] == "ambiguous"
    assert len(ambiguous["candidates"]) == 2
    conflict = source_dates(source(text, published_at=date(2026, 7, 28)))[
        "published_at"
    ]
    assert conflict["date"] is None and conflict["basis"] == "conflict"
    assert conflict["metadata_date"] == "2026-07-28"


def test_cutoff_marks_future_body_date_without_erasing_it_or_claiming_history():
    original = source("发布日期：2026年7月28日\nquarter ended 2026-06-25")
    before = source_dates(original, date(2026, 7, 27))
    after = source_dates(original, date(2026, 7, 28))
    assert before["published_at"]["after_cutoff"] is True
    assert after["published_at"]["after_cutoff"] is False
    assert before["published_at"]["date"] == after["published_at"]["date"]
    assert "historical_availability_verified" not in before["published_at"]


def test_invalid_date_is_unknown_and_comparative_quarters_remain_ambiguous():
    dates = source_dates(
        source(
            "Published: February 30, 2026\nquarter ended June 25, 2026\n"
            "quarter ended June 26, 2025"
        )
    )
    assert dates["published_at"]["date"] is None
    assert dates["report_date"]["date"] is None
    assert dates["report_date"]["basis"] == "ambiguous"
    assert quarter_ends("Three months ended Jun. 25, 2026") == {
        date(2026, 6, 25): "Three months ended Jun. 25, 2026"
    }


def test_candidate_text_uses_one_consistent_entity_normalization_pass():
    original = source(
        "Published: July&nbsp;28, 2026\n"
        "quarter ended June&nbsp;25, 2026\n"
        "Literal entity example: quarter ended June&amp;nbsp;26, 2025"
    )
    dates = source_dates(original)
    assert dates["report_date"]["date"] == "2026-06-25"
    for support in dates.values():
        for candidate in support["candidates"]:
            assert candidate["text"] in html.unescape(original.snippets[0].content)
