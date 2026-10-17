"""Check fund identity, NAV calendars, distributions and reproducible risk measures."""

import json
from datetime import date, datetime

import pytest

from research_agent.config import ResearchError
from research_agent.tools.fund_data import (
    CHINA,
    fund_code,
    js_value,
    parse_documents,
    parse_nav,
    parse_profile,
    share_class,
)
from research_agent.tools.fund_metrics import compare_nav, summarize_nav


def script(code="110020", rows=None, name="示例沪深300ETF联接A"):
    return (
        f"var fS_name = {json.dumps(name)}; var fS_code = {json.dumps(code)}; "
        f"var Data_netWorthTrend = {json.dumps(rows or [])};"
    )


def observation(day, nav, growth=0, event=""):
    return {
        "x": int(datetime.fromisoformat(day).replace(tzinfo=CHINA).timestamp() * 1000),
        "y": nav,
        "equityReturn": growth,
        "unitMoney": event,
    }


@pytest.mark.parametrize("bad", [110020, "12345", "../110020", "１１００２０", None])
def test_fund_code_cannot_lose_leading_zeros_or_become_a_path(bad):
    assert fund_code("000051") == "000051"
    with pytest.raises(ResearchError):
        fund_code(bad)


def test_profile_fields_verify_code_subtype_and_selected_share_class():
    html = """<table><tr><th>基金简称</th><td>示例沪深300ETF联接C</td>
    <th>基金代码</th><td>007339（前端）</td></tr>
    <tr><th>基金类型</th><td>指数型-股票</td><th>销售服务费率</th><td>0.20%（每年）</td></tr></table>"""
    profile = parse_profile(html, "007339")
    assert profile["share_class"] == "C" and profile["fund_type"] == "etf_feeder"
    assert profile["fees_percent"]["sales_service"] == 0.2
    with pytest.raises(ResearchError, match="profile code"):
        parse_profile(html, "110020")
    assert share_class("示例沪深300ETF") is None
    assert share_class("示例海外ETF(QDII)") is None
    assert share_class("示例沪深300ETF联接A") == "A"


def test_nav_dates_use_china_timezone_and_exclude_future_observations():
    rows = [
        observation("2026-01-01", 1),
        observation("2026-01-02", 1.1, 10),
        observation("2026-01-03", 8, 627.27),
    ]
    _, selected, raw = parse_nav(script(rows=rows), "110020", date(2026, 1, 2))
    assert [row["date"] for row in selected] == ["2026-01-01", "2026-01-02"]
    assert len(raw) == 2 and selected[-1]["nav"] == 1.1
    with pytest.raises(ResearchError, match="code differs"):
        parse_nav(script(code="007339", rows=rows), "110020", date(2026, 1, 3))


@pytest.mark.parametrize("bad_nav", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_navs_are_gaps_not_silently_filled(bad_nav):
    rows = [observation("2026-01-01", 1), observation("2026-01-02", bad_nav)]
    with pytest.raises(ResearchError):
        parse_nav(script(rows=rows), "110020", date(2026, 1, 2))


def test_provider_javascript_is_never_executed():
    with pytest.raises(ResearchError):
        js_value(
            "var Data_netWorthTrend = fetch('https://unread.test/');",
            "Data_netWorthTrend",
        )


def test_distribution_day_does_not_create_a_false_nav_drawdown():
    rows = [
        {"date": "2026-01-01", "nav": 1, "daily_return_pct": 0, "event": ""},
        {
            "date": "2026-01-02",
            "nav": 0.9,
            "daily_return_pct": 0,
            "event": "分红：每份派现金0.10元",
        },
        {"date": "2026-01-03", "nav": 0.99, "daily_return_pct": 10, "event": ""},
    ]
    result = summarize_nav(rows)
    assert result["periods"]["available_history"]["return_pct"] == pytest.approx(10)
    assert result["periods"]["available_history"]["max_drawdown_pct"] == 0
    assert result["distribution_event_dates"] == ["2026-01-02"]
    rows[1]["daily_return_pct"] = None
    with pytest.raises(ResearchError):
        summarize_nav(rows)


def test_daily_return_conflict_is_rejected_but_nonconsecutive_observations_use_nav_ratios():
    rows = [
        {"date": "2013-03-06", "nav": 1, "daily_return_pct": 0, "event": ""},
        {"date": "2013-03-08", "nav": 1.0029, "daily_return_pct": 0, "event": ""},
    ]
    assert summarize_nav(rows)["periods"]["available_history"][
        "return_pct"
    ] == pytest.approx(0.29)
    rows[1].update(date="2013-03-07", nav=0.5)
    with pytest.raises(ResearchError) as error:
        summarize_nav(rows)
    assert error.value.code == "fund_return_conflict"


def test_drawdown_recovery_and_volatility_have_known_results_and_no_short_period_annualization():
    rows = [
        {"date": "2026-01-01", "nav": 1, "daily_return_pct": None, "event": ""},
        {"date": "2026-01-02", "nav": 1.1, "daily_return_pct": None, "event": ""},
        {"date": "2026-01-03", "nav": 0.99, "daily_return_pct": None, "event": ""},
    ]
    metrics = summarize_nav(rows)["periods"]["available_history"]
    assert metrics["return_pct"] == pytest.approx(-1)
    assert metrics["max_drawdown_pct"] == pytest.approx(-10)
    assert metrics["annualized_volatility_pct"] == pytest.approx(224.499443)
    assert metrics["annualized_return_pct"] is None and metrics["recovery_date"] is None
    rows.append(
        {"date": "2026-01-04", "nav": 1.1, "daily_return_pct": None, "event": ""}
    )
    assert (
        summarize_nav(rows)["periods"]["available_history"]["recovery_date"]
        == "2026-01-04"
    )


def test_comparison_uses_common_dates_and_does_not_rename_etf_as_official_benchmark():
    def rows(values):
        return [
            {"date": day, "nav": value, "daily_return_pct": None, "event": ""}
            for day, value in values
        ]

    left = rows(
        [
            ("2026-01-01", 1),
            ("2026-01-02", 1.1),
            ("2026-01-03", 1.21),
            ("2026-01-04", 1.089),
        ]
    )
    right = rows([("2026-01-01", 2), ("2026-01-03", 2.2), ("2026-01-04", 2.2)])
    result = compare_nav(left, right)
    period = result["periods"]["common_history"]
    assert period["observations"] == 3 and period["end_date"] == "2026-01-04"
    assert period["fund_return_pct"] == pytest.approx(8.9)
    assert period["cumulative_difference_pp"] == pytest.approx(-1.1)
    assert "not the official" in result["comparison_role"]


def test_disclosures_filter_future_dates_and_remain_discovery_only():
    records = [
        {
            "FUNDCODE": "110020",
            "TITLE": "示例基金2026年中期报告",
            "PUBLISHDATE": "2026-08-31",
            "ID": "AN202608311828748489",
        },
        {
            "FUNDCODE": "110020",
            "TITLE": "示例基金2026年第3季度报告",
            "PUBLISHDATE": "2026-10-30",
            "ID": "AN202610301828748489",
        },
        {
            "FUNDCODE": "007339",
            "TITLE": "其他基金产品资料概要",
            "PUBLISHDATE": "2026-08-11",
            "ID": "AN202608111828748489",
        },
    ]
    result = parse_documents({"Data": records}, "110020", date(2026, 10, 15))
    assert len(result) == 1 and result[0]["published_at"] == "2026-08-31"
    assert result[0]["coverage"] == "discovery_only"
