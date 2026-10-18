"""Check fund identity, NAV calendars, distributions and reproducible risk measures."""

import json
from datetime import date, datetime

import pytest

from research_agent.config import ResearchError
from research_agent.tools.fund_data import (
    CHINA,
    fund_code,
    js_value,
    parse_benchmark_disclosure,
    parse_documents,
    parse_nav,
    parse_profile,
    parse_share_class_fees,
    share_class,
)
from research_agent.tools.fund_metrics import (
    compare_nav,
    reconcile_benchmark_disclosure,
    summarize_nav,
)


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
    assert metrics["recovery_calendar_days_from_peak"] is None
    rows.append(
        {"date": "2026-01-04", "nav": 1.1, "daily_return_pct": None, "event": ""}
    )
    recovered = summarize_nav(rows)["periods"]["available_history"]
    assert recovered["recovery_date"] == "2026-01-04"
    assert recovered["recovery_calendar_days_from_peak"] == 2
    assert recovered["recovery_calendar_days_from_trough"] == 1


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


@pytest.mark.parametrize(
    "name,category,expected",
    [
        ("示例沪深300指数精选增强A", "指数型-股票", "enhanced_index"),
        ("示例回报增强混合A", "混合型-偏股", "mixed"),
        ("示例收益增强债券A", "债券型-混合债", "unsupported"),
    ],
)
def test_enhancement_label_requires_an_index_product(name, category, expected):
    html = f"<table><tr><th>基金代码</th><td>010736</td><th>基金简称</th><td>{name}</td></tr><tr><th>基金类型</th><td>{category}</td></tr></table>"
    assert parse_profile(html, "010736")["fund_type"] == expected


def test_full_fund_name_can_identify_enhancement_hidden_by_a_short_alias():
    html = """<table><tr><th>基金代码</th><td>163407</td>
    <th>基金简称</th><td>示例沪深300指数(LOF)A</td></tr>
    <tr><th>基金全称</th><td>示例沪深300指数增强型证券投资基金(LOF)</td>
    <th>基金类型</th><td>指数型-股票</td></tr></table>"""
    assert parse_profile(html, "163407")["fund_type"] == "enhanced_index"


@pytest.fixture
def disclosed_performance():
    return """示例基金010736中期报告
基金合同生效日 2024年6月30日
报告期末(2026年6月30日)
业绩比较基准 指数收益率×95%+存款利率×5%
3.2 基金净值表现 ........................ 7
§4 管理人报告 ........................ 10
3.2 基金净值表现
3.2.1 基金份额净值增长率及其与同期业绩比较基准收益率的比较
示例指数增强 A
阶段 份额净值增长率 标准差 基准收益率 标准差 差异 差异
过去三个月 12.00% 1.20% 5.00% 0.80% 7.00% 0.40%
过去六个月 10.00% 1.10% 8.00% 0.90% 2.00% 0.20%
自基金合同生
效起至今 21.00% 1.00% 20.00% 0.80% 1.00% 0.20%
示例指数增强 C
过去三个月 11.90% 1.20% 5.00% 0.80% 6.90% 0.40%
3.2.2 累计净值图
过去三个月 99.00% 1.20% 5.00% 0.80% 94.00% 0.40%
本基金年化跟踪误差不超过6%。报告期年化跟踪误差7.00%，超过目标控制范围。
"""


def test_official_performance_skips_contents_and_other_classes_without_fabricating_ir(
    disclosed_performance,
):
    result = parse_benchmark_disclosure(disclosed_performance, "A")
    assert result["report_end_date"] == "2026-06-30"
    assert result["inception_date"] == "2024-06-30"
    assert result["definition"] == "指数收益率×95%+存款利率×5%"
    assert [row["fund_return_pct"] for row in result["periods"]] == [12, 10, 21]
    assert result["periods"][0]["return_difference_pp"] == 7
    assert result["tracking_risk"] == {
        "tracking_error_target_pct": 6,
        "reported_tracking_error_pct": 7,
        "target_exceeded": True,
    }
    assert result["information_ratio"] is None and not result["daily_series_available"]
    assert (
        parse_benchmark_disclosure(disclosed_performance, "C")["periods"][0][
            "fund_return_pct"
        ]
        == 11.9
    )
    assert not parse_benchmark_disclosure(disclosed_performance, "Y")["periods"]


def test_ambiguous_or_undated_disclosed_returns_stay_gaps(disclosed_performance):
    undated = disclosed_performance.replace("报告期末(2026年6月30日)", "")
    assert not parse_benchmark_disclosure(undated, "A")["periods"]
    broken = disclosed_performance.replace("7.00% 0.40%", "9.00% 0.40%")
    result = parse_benchmark_disclosure(broken, "A")
    assert all(row["period"] != "过去三个月" for row in result["periods"])
    assert any("inconsistent" in gap for gap in result["gaps"])
    missing = disclosed_performance.replace(
        "报告期年化跟踪误差7.00%", "报告期跟踪风险未披露"
    )
    risk = parse_benchmark_disclosure(missing, "A")["tracking_risk"]
    assert "reported_tracking_error_pct" not in risk and "target_exceeded" not in risk


def test_disclosed_nav_reconciliation_uses_month_end_and_preserves_conflicts():
    rows = [
        {"date": day, "nav": nav, "daily_return_pct": None, "event": ""}
        for day, nav in [("2025-12-31", 1), ("2026-03-31", 1.02), ("2026-06-30", 1.1)]
    ]
    disclosure = {
        "report_end_date": "2026-06-30",
        "gaps": [],
        "periods": [
            {"period": "过去三个月", "fund_return_pct": 7.84},
            {"period": "过去六个月", "fund_return_pct": 9},
        ],
    }
    result = reconcile_benchmark_disclosure(rows, disclosure)
    three, six = result["periods"]
    assert three["nav_reconciliation"]["start_date"] == "2026-03-31"
    assert three["nav_reconciliation"]["consistent_with_rounding"]
    assert six["nav_reconciliation"]["calculated_return_pct"] == 10
    assert not six["nav_reconciliation"]["consistent_with_rounding"]
    assert result["gaps"] and six["fund_return_pct"] == 9


def test_conflicting_return_rows_do_not_hide_an_independent_tracking_risk(
    disclosed_performance,
):
    duplicate = "示例指数增强 A\n过去三个月 13.00% 1.20% 5.00% 0.80% 8.00% 0.40%\n"
    text = disclosed_performance.replace(
        "3.2.2 累计净值图", duplicate + "3.2.2 累计净值图"
    )
    result = parse_benchmark_disclosure(text, "A")
    assert all(row["period"] != "过去三个月" for row in result["periods"])
    assert result["tracking_risk"]["target_exceeded"]
    assert any("conflicting" in gap for gap in result["gaps"])


@pytest.mark.parametrize(
    "category", ["混合型-灵活", "混合型-偏股", "混合型-偏债", "混合型-平衡"]
)
def test_mixed_categories_are_enabled_without_accepting_bond_or_fof(category):
    def html(value):
        return f"<table><tr><th>基金代码</th><td>021528</td><th>基金简称</th><td>示例成长混合C</td></tr><tr><th>基金类型</th><td>{value}</td></tr></table>"

    assert parse_profile(html(category), "021528")["fund_type"] == "mixed"
    for value in ("债券型-混合债", "FOF-混合型", "混合型-FOF"):
        assert parse_profile(html(value), "021528")["fund_type"] == "unsupported"


def test_previous_document_uses_distinct_reporting_period_not_publication_date():
    records = [
        {"FUNDCODE": "021528", "TITLE": title, "PUBLISHDATE": day, "ID": identity}
        for title, day, identity in [
            ("示例基金2026年中期报告", "2026-08-29", "AN202608291828682176"),
            ("示例基金2026年第2季度报告", "2026-07-21", "AN202607211827195457"),
            ("示例基金2026年第1季度报告", "2026-04-22", "AN202604221820000001"),
            ("示例基金2026年第3季度报告", "2026-10-30", "AN202610301820000001"),
        ]
    ]
    selected = parse_documents(
        {"Data": records}, "021528", date(2026, 10, 16), include_previous=True
    )
    assert len(selected) == 2
    assert selected[0]["report_period_hint"] == "2026-06-30"
    assert (
        selected[1]["previous_periodic"]
        and selected[1]["report_period_hint"] == "2026-03-31"
    )
    assert len(parse_documents({"Data": records}, "021528", date(2026, 10, 16))) == 1


def test_c_class_history_and_wrapped_benchmark_do_not_inherit_parent_history():
    text = """基金合同生效日2015年6月29日
本基金自2024年5月28日起增加收取销售服务费的C类基金份额。
报告期末(2026年6月30日)
业绩比较基准
沪深300指数收益率×55%+上证国债指数收益
率×45%
风险收益特征
混合基金。
3.2.1 基金份额净值增长率与基准比较
示例成长混合A
过去三年 555% 3% 20% 0.6% 535% 2.4%
示例成长混合C
过去六个月 50% 3% 5% 0.6% 45% 2.4%
过去三年 - - - - - -
自增加C类
基金份额起
至今 200% 3% 20% 0.6% 180% 2.4%
3.2.2 净值图
对照增强基金年化跟踪误差不超过6%。
"""
    disclosure = parse_benchmark_disclosure(text, "C", include_tracking_risk=False)
    assert disclosure["definition"] == "沪深300指数收益率×55%+上证国债指数收益率×45%"
    assert disclosure["inception_date"] == "2015-06-29"
    assert disclosure["selected_share_class_start_date"] == "2024-05-28"
    assert len(disclosure["periods"]) == 2 and not disclosure["tracking_risk"]
    rows = [
        {"date": day, "nav": value, "daily_return_pct": None, "event": ""}
        for day, value in [("2024-05-28", 1), ("2025-12-31", 2), ("2026-06-30", 3)]
    ]
    result = reconcile_benchmark_disclosure(rows, disclosure)
    assert result["periods"][-1]["nav_reconciliation"]["start_date"] == "2024-05-28"
    assert all(
        row["nav_reconciliation"]["consistent_with_rounding"]
        for row in result["periods"]
    )


def test_c_class_combined_summary_preserves_actual_a_subscription_and_c_fee():
    text = """基金销售相关费用
示例成长混合A
申购费 1.50% 1.00% 0.50%
赎回费 1.50% 0.75% 0.50% 0.25% 0.00%
示例成长混合C
赎回费 1.50% 0.50% 0.00%
基金运作相关费用
销售服务费 C 0.40%
A类基金份额不收取销售服务费。
"""
    fees = parse_share_class_fees(text, "示例成长混合C", "C", summary=True)
    assert fees == {
        "A": {"sales_service": 0, "maximum_subscription": 1.5},
        "C": {"sales_service": 0.4},
    }
    c_only = "示例成长混合C 申购费0.00% 赎回费1.50% 销售服务费C0.40%"
    assert (
        "maximum_subscription"
        not in parse_share_class_fees(c_only, "示例成长混合C", "C", summary=True)["A"]
    )
