"""Check the shared fund path without model providers, SEC access or live network."""

import io
import json
import sys
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import pytest
from fakes import ToolCallingFakeModel
from langchain_core.messages import AIMessage
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from research_agent import mvp, runner
from research_agent.config import ResearchBudget, ResearchError, Settings
from research_agent.runtime.reporting import (
    check_report,
    insert_fund_costs,
    insert_mixed_performance,
)
from research_agent.skill_loader import load_skill
from research_agent.tools.fund import FundTools
from research_agent.tools.fund_data import CHINA
from research_agent.tools.pdf import pdf_window, read_pdf
from research_agent.tools.webpage import HttpFetcher


def fund_report(code="110020", kind="etf_feeder"):
    return f"# 示例基金（{code}）研究报告\n\n" + "\n\n".join(
        f"## {index}. {heading}\n\n截至2026-10-08，净值为1.2元；未知费用与估值保留为缺口。"
        f"[基金资料](https://fundf10.eastmoney.com/jbgk_{code}.html)"
        for index, heading in enumerate(
            load_skill(kind="fund", fund_type=kind).report_headings, 1
        )
    )


@pytest.fixture
def fund_case(tmp_path):
    clients = []

    def build(
        responses,
        *,
        code="110020",
        kind="etf_feeder",
        requested_class="A",
        actual_class="A",
        report_text=None,
        nav_rows=None,
    ):
        settings = Settings()
        budget = ResearchBudget(settings)
        seen = []
        suffix = {
            "etf_feeder": "ETF联接",
            "passive_index": "指数",
            "enhanced_index": "指数增强",
            "mixed": "混合",
        }[kind]
        name = f"示例沪深300{suffix}{actual_class}"
        category = "混合型-灵活" if kind == "mixed" else "指数型-股票"

        def respond(request):
            url = "https://" + request.headers["Host"] + request.url.raw_path.decode()
            seen.append(url)
            if "/jbgk_" in url:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text=(
                        f"<table><tr><th>基金代码</th><td>{code}（前端）</td>"
                        f"<th>基金简称</th><td>{name}</td></tr>"
                        f"<tr><th>基金类型</th><td>{category}</td>"
                        "<th>基金管理人</th><td>示例基金</td></tr></table>"
                    ),
                )
            if "/pingzhongdata/" in url:
                rows = [
                    {
                        "x": int(datetime(2025, 1, 2, tzinfo=CHINA).timestamp() * 1000),
                        "y": 1,
                        "equityReturn": 0,
                        "unitMoney": "",
                    },
                    {
                        "x": int(
                            datetime(2026, 10, 8, tzinfo=CHINA).timestamp() * 1000
                        ),
                        "y": 1.2,
                        "equityReturn": 0,
                        "unitMoney": "",
                    },
                    {
                        "x": int(
                            datetime(2026, 10, 10, tzinfo=CHINA).timestamp() * 1000
                        ),
                        "y": 8,
                        "equityReturn": 0,
                        "unitMoney": "",
                    },
                ]
                selected = request.url.path.rsplit("/", 1)[-1].removesuffix(".js")
                selected_name = name if selected == code else "示例沪深300ETF"
                rows = rows if nav_rows is None else nav_rows
                text = f"var fS_code={json.dumps(selected)};var fS_name={json.dumps(selected_name)};var Data_netWorthTrend={json.dumps(rows)};"
                return httpx.Response(
                    200, headers={"content-type": "application/javascript"}, text=text
                )
            if "/f10/JJGG" in url:
                records = (
                    [
                        {
                            "FUNDCODE": code,
                            "TITLE": "示例基金2026年中期报告",
                            "PUBLISHDATE": "2026-08-31",
                            "ID": "AN202608311828748047",
                        }
                    ]
                    if report_text
                    else []
                )
                return httpx.Response(200, json={"Data": records})
            if (
                url == "https://pdf.dfcfw.com/pdf/H2_AN202608311828748047_1.pdf"
                and report_text
            ):
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"%PDF-mocked text extraction",
                )
            raise AssertionError(f"Unexpected source request: {url}")

        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        fetcher = HttpFetcher(
            settings, budget, client=client, resolve_host=lambda host: ["93.184.216.34"]
        )
        search = SimpleNamespace(budget=budget, close=lambda: None)
        model = ToolCallingFakeModel(responses=responses)
        run = mvp.MvpRun(
            settings,
            "研究示例基金",
            code,
            date(2026, 10, 9),
            tmp_path / str(len(clients)),
            target_type="fund",
            fund_type=kind,
            share_class=requested_class,
            model=model,
            fetcher=fetcher,
            search=search,
        )
        return run, model, seen

    yield build
    for client in clients:
        client.close()


@pytest.mark.parametrize(
    "kind,code",
    [
        ("etf_feeder", "110020"),
        ("passive_index", "000051"),
        ("enhanced_index", "010736"),
        ("mixed", "021528"),
    ],
)
def test_fund_branch_keeps_one_model_and_passes_canonical_data_to_fund_synthesis(
    fund_case, kind, code
):
    def research(messages):
        packet = json.loads(messages[1].content)
        assert packet["fund_code"] == code and packet["share_class"] == "A"
        assert "initial_sec_discovery" not in packet
        assert "company revenue/EPS forecasts or DCF" in messages[0].content
        return AIMessage(
            content="已读取基金资料；费用与底层估值仍有缺口，保留观察条件。"
        )

    def synthesis(messages):
        packet = json.loads(messages[1].content)
        assert packet["fund_data"]["identity_verified"]
        assert packet["report_date"]
        assert packet["read_sources"][0]["retrieved_at"]
        data = packet["fund_data"]["nav_metrics"][code]
        assert (
            data["latest_nav_date"] == "2026-10-08" and data["latest_unit_nav"] == 1.2
        )
        assert "ticker" not in packet and packet["fund_type"] == kind
        return AIMessage(content=fund_report(code, kind))

    run, model, seen = fund_case([research, synthesis], code=code, kind=kind)
    result = run.run()
    assert result["status"] == "generated" and result["quality_accepted"] is None
    assert model.i == 2 and result["calls"] == {"model": 2, "request": 3, "tool": 3}
    assert not any("sec.gov" in url for url in seen)
    assert (run.directory / "report.md").exists()
    saved = json.loads((run.directory / "fund_data.json").read_text(encoding="utf-8"))
    assert (
        saved["identity_verified"]
        and saved["nav_metrics"][code]["latest_unit_nav"] == 1.2
    )
    assert not (run.directory / "2026-10-13-GOOG.md").exists()
    assert not (run.directory / "sources").exists()
    raw = json.loads((run.directory / f"nav-{code}.json").read_text(encoding="utf-8"))
    assert len(raw["raw_rows"]) == 2
    assert (
        run.source_tools.fund_data["nav_metrics"][code]["periods"]["available_history"][
            "return_pct"
        ]
        == 20
    )
    assert not run.source_tools.fund_data["nav_metrics"][code]["periods"][
        "available_history"
    ]["annualized_volatility_pct"]


def test_share_class_mismatch_stops_before_spending_model_calls_or_whitelisting_wrong_identity(
    fund_case,
):
    run, model, _ = fund_case([], requested_class="C", actual_class="A")
    result = run.run()
    assert result["status"] == "failed" and not result["report_written"]
    assert model.i == 0 and result["calls"]["model"] == 0
    assert run.source_tools.allowed_urls == set()


def test_fund_research_model_failure_keeps_numeric_inputs_and_still_delivers(fund_case):
    def broken(messages):
        raise RuntimeError("private model failure")

    run, model, _ = fund_case([broken, AIMessage(content=fund_report())])
    result = run.run()
    assert (
        result["status"] == "generated" and result["errors"][0]["stage"] == "research"
    )
    assert (run.directory / "nav-110020.json").exists()
    assert "private model failure" not in (run.directory / "result.json").read_text(
        encoding="utf-8"
    )


def test_enhanced_risk_disclosure_survives_research_failure_and_reaches_synthesis(
    fund_case, monkeypatch
):
    from research_agent.tools import fund as fund_module

    text = """示例基金010736
基金合同生效日2025年1月2日
报告期末(2026年6月30日)
业绩比较基准沪深300指数收益率×95%+存款利率×5%
3.2.1 基金份额净值增长率与基准比较
示例指数增强 A
过去六个月 10% 1% 8% 0.8% 2% 0.2%
3.2.2 净值图
本基金年化跟踪误差不超过6%。报告期年化跟踪误差15.46%，超过目标控制范围。
"""
    monkeypatch.setattr(fund_module, "read_pdf", lambda body, budget: ([text], 1))

    def broken(messages):
        assert "enhancement process" in messages[0].content
        raise RuntimeError("research failed")

    def synthesis(messages):
        packet = json.loads(messages[1].content)
        disclosure = packet["fund_data"]["benchmark_disclosures"][0]
        assert disclosure["periods"][0]["return_difference_pp"] == 2
        assert disclosure["tracking_risk"]["target_exceeded"]
        assert disclosure["tracking_risk"]["reported_tracking_error_pct"] == 15.46
        assert disclosure["information_ratio"] is None
        assert "target_exceeded=true" in messages[0].content
        assert "target_etf" not in packet["fund_data"]
        return AIMessage(content=fund_report("010736", "enhanced_index"))

    run, model, seen = fund_case(
        [broken, synthesis], code="010736", kind="enhanced_index", report_text=text
    )
    result = run.run()
    assert (
        result["status"] == "generated" and result["errors"][0]["stage"] == "research"
    )
    assert model.i == 2 and not any("sec.gov" in url for url in seen)
    saved = json.loads((run.directory / "fund_data.json").read_text(encoding="utf-8"))
    assert saved["benchmark_disclosures"][0]["tracking_risk"]["target_exceeded"]
    assert (run.directory / "report.md").exists()
    assert (run.directory / "nav-010736.json").exists()


def test_enhanced_etf_comparison_is_not_labeled_as_a_designated_target(fund_case):
    rows = [
        {
            "x": int(datetime(2026, 10, day, tzinfo=CHINA).timestamp() * 1000),
            "y": nav,
            "equityReturn": growth,
            "unitMoney": "",
        }
        for day, nav, growth in [(6, 1, 0), (7, 1.1, 10), (8, 1.21, 10)]
    ]
    run, _, _ = fund_case([], code="010736", kind="enhanced_index", nav_rows=rows)
    tools = run.source_tools
    tools.get_fund_profile()
    result = tools.get_nav_history(comparison_code="510310")
    assert "error" not in result
    role = result["comparison"]["comparison_role"]
    assert "not the official benchmark" in role
    assert "not a designated target ETF" in role
    assert tools.fund_data["comparisons"]["010736/510310"]["comparison_role"] == role


@pytest.mark.parametrize(
    "kind,code,share",
    [
        ("passive_index", "000051", "A"),
        ("enhanced_index", "010736", "A"),
        ("mixed", "021528", "C"),
    ],
)
def test_cli_fund_arguments_preserve_code_and_class_and_explicit_route(
    tmp_path, monkeypatch, capfd, kind, code, share
):
    calls = []
    monkeypatch.setattr(
        mvp,
        "run_mvp",
        lambda settings, **kw: calls.append(kw) or {"status": "generated"},
    )
    assert (
        runner.main(
            [
                "研究基金",
                "--target-type",
                "fund",
                "--fund-code",
                code,
                "--fund-type",
                kind,
                "--share-class",
                share.lower(),
                "--market",
                "CN",
                "--as-of",
                "2026-10-09",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert calls[0]["ticker"] == code and calls[0]["share_class"] == share
    assert calls[0]["target_type"] == "fund" and calls[0]["fund_type"] == kind


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--fund-type", "active", "--share-class", "A"],
        ["--fund-type", "etf_feeder"],
        ["--fund-type", "etf_feeder", "--share-class", "A", "--scope", "focused"],
    ],
)
def test_unsupported_fund_cli_scope_stops_before_provider_access(monkeypatch, extra):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unsupported input must not run.")

    monkeypatch.setattr(mvp, "run_mvp", forbidden)
    with pytest.raises(SystemExit):
        runner.main(
            ["研究基金", "--target-type", "fund", "--fund-code", "110020", *extra]
        )


@pytest.mark.parametrize(
    "kind,code,share",
    [
        ("etf_feeder", "110020", "A"),
        ("enhanced_index", "010736", "A"),
        ("mixed", "021528", "C"),
    ],
)
def test_fund_inputs_roundtrip_into_the_existing_worker(
    tmp_path, monkeypatch, kind, code, share
):
    captured = {}

    def supervise(command, directory, seconds, *, report_filename):
        assert report_filename == "report.md"
        captured["command"] = command
        captured["inputs"] = json.loads(
            (directory / "inputs.json").read_text(encoding="utf-8")
        )
        captured["directory"] = directory
        return {"status": "generated"}

    monkeypatch.setattr(mvp, "supervise_process", supervise)
    mvp.run_mvp(
        Settings(),
        query="研究基金",
        ticker=code,
        as_of=date(2026, 10, 9),
        output_dir=tmp_path,
        target_type="fund",
        fund_type=kind,
        share_class=share,
    )
    assert captured["inputs"]["target_type"] == "fund"
    assert "references/fund-memo-template.md" in captured["inputs"]["skill_files"]

    class Worker:
        def __init__(self, *args, **kwargs):
            captured["worker_args"], captured["worker_kwargs"] = args, kwargs

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(mvp, "MvpRun", Worker)
    monkeypatch.setattr(sys, "argv", ["mvp", "--worker", str(captured["directory"])])
    mvp.main()
    assert captured["worker_args"][2] == code
    assert captured["worker_kwargs"] == {
        "target_type": "fund",
        "fund_type": kind,
        "share_class": share,
    }
    assert captured["ran"]


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Fund 110020 annual disclosure) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_text_pdf_uses_bounded_fetch_and_only_registers_successful_readings(tmp_path):
    settings = Settings(max_requests=2)
    budget = ResearchBudget(settings)
    data = text_pdf()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=data
            )
        )
    )
    fetcher = HttpFetcher(
        settings, budget, client=client, resolve_host=lambda host: ["93.184.216.34"]
    )
    tools = FundTools(
        settings,
        budget,
        fetcher,
        None,
        tmp_path,
        date(2026, 10, 9),
        "110020",
        fund_type="etf_feeder",
        share_class="A",
    )
    url = "https://example.test/20260831/fund.pdf"
    result = tools.read_source(url)
    assert "Fund 110020" in result["text"] and result["pages"] == [1]
    assert url in tools.allowed_urls and budget.counts["request"] == 1
    future = tools.read_source("https://example.test/20261030/fund.pdf")
    assert future["error"] == "future_source" and budget.counts["request"] == 1
    assert (
        "body" not in fetcher.get(url, content_types=("application/pdf",)).model_dump()
    )
    client.close()


def test_pdf_focus_skips_contents_and_blank_pdf_is_an_explicit_gap():
    result = pdf_window(
        ["重要提示及目录 投资组合报告 ........ 50", "投资组合报告 verified holdings"],
        2,
        focus="投资组合报告",
    )
    assert result["pages"] == [2] and "verified holdings" in result["text"]
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ResearchError) as error:
        read_pdf(output.getvalue(), ResearchBudget(Settings()))
    assert error.value.code == "pdf_no_text"


def test_pdf_section_selection_prefers_a_heading_over_an_earlier_prose_reference():
    pages = [
        "1.2目录\n§7投资组合报告................49",
        "5.3 托管人对基金投资组合报告等内容的意见\n托管人监督复核基金投资组合报告，并检查估值流程。",
        "§7 投资组合报告\n7.1 期末基金资产组合情况\n7.2 行业分类",
    ]
    assert pdf_window(pages, 3, focus="投资组合报告")["pages"][0] == 3
    assert pdf_window(pages, 3, focus="§7.2")["pages"][0] == 3
    assert pdf_window(pages, 3, start_page=2, max_chars=20)["pages"][0] == 2
    with pytest.raises(ResearchError):
        pdf_window(pages, 3, start_page=4)


def test_ac_cost_scenarios_use_actual_fees_and_reject_unverified_share_relationship(
    tmp_path,
):
    settings = Settings()
    budget = ResearchBudget(settings)
    tools = FundTools(
        settings,
        budget,
        None,
        None,
        tmp_path,
        date(2026, 10, 15),
        "110020",
        fund_type="etf_feeder",
        share_class="A",
    )
    a = {
        "code": "110020",
        "share_class": "A",
        "full_name": "同一基金",
        "source_url": "https://example.test/A",
        "fees_percent": {"maximum_subscription": 1.2, "sales_service": 0},
    }
    c = {
        "code": "007339",
        "share_class": "C",
        "full_name": "同一基金",
        "source_url": "https://example.test/C",
        "fees_percent": {"sales_service": 0.25},
    }
    tools.fund_data["profiles"] = {"110020": a, "007339": c}
    tools._share_costs()
    result = tools.fund_data["share_class_costs"]
    assert result["C_sales_service_pct_per_year"] == 0.25
    assert [row["break_even_years"] for row in result["scenarios"]] == [4.8, 0.48]
    tools.fund_data.pop("share_class_costs")
    a["full_name"] = None
    c["full_name"] = None
    tools._share_costs()
    assert "share_class_costs" not in tools.fund_data
    a["full_name"] = c["full_name"] = "同一基金"
    a["fees_percent"] = c["fees_percent"] = {}
    tools._dated_fees = {
        "A": {"maximum_subscription": 1.2, "sales_service": 0},
        "C": {"sales_service": 0.2},
    }
    tools._share_costs()
    assert [
        row["break_even_years"]
        for row in tools.fund_data["share_class_costs"]["scenarios"]
    ] == [6, 0.6]
    markdown = fund_report()
    bundle = load_skill(kind="fund", fund_type="etf_feeder")
    output = insert_fund_costs(markdown, tools.fund_data, bundle.report_headings)
    assert "C类 0.2%/年" in output and "| 1.2% | 6 年 |" in output
    assert output.count("### A/C费用情景与复算口径") == 1
    assert insert_fund_costs(output, tools.fund_data, bundle.report_headings) == output
    assert not check_report(
        output,
        bundle.report_headings,
        {
            URL
            for URL in [
                "https://fundf10.eastmoney.com/jbgk_110020.html",
                a["source_url"],
                c["source_url"],
            ]
        },
    )["errors"]


def test_mixed_c_keeps_its_history_and_composite_benchmark_after_research_failure(
    fund_case, monkeypatch
):
    from research_agent.tools import fund as fund_module

    text = """示例基金021528
基金合同生效日2015年6月29日
本基金自2024年5月28日起增加收取销售服务费的C类基金份额。
报告期末(2026年6月30日)
业绩比较基准
沪深300指数收益率×55%+上证国债指数收益率×45%
风险收益特征
混合基金。
3.2.1 基金份额净值增长率与基准比较
示例成长混合A
过去三年 555% 3% 20% 0.6% 535% 2.4%
示例成长混合C
过去六个月 50% 3% 5% 0.6% 45% 2.4%
自增加C类基金份额起至今 200% 3% 20% 0.6% 180% 2.4%
3.2.2 净值图
"""
    monkeypatch.setattr(fund_module, "read_pdf", lambda body, budget: ([text], 1))
    rows = [
        {
            "x": int(
                datetime.fromisoformat(day).replace(tzinfo=CHINA).timestamp() * 1000
            ),
            "y": nav,
            "equityReturn": None,
            "unitMoney": "",
        }
        for day, nav in [
            ("2024-05-28", 1),
            ("2025-12-31", 2),
            ("2026-06-30", 3),
            ("2026-10-08", 1.2),
        ]
    ]

    def research(messages):
        assert "mixed fund's contractual" in messages[0].content
        raise RuntimeError("research interrupted")

    def synthesis(messages):
        packet = json.loads(messages[1].content)
        assert packet["fund_type"] == "mixed" and packet["share_class"] == "C"
        data = packet["fund_data"]
        metrics = data["nav_metrics"]["021528"]
        assert metrics["first_available_date"] == "2024-05-28"
        assert "three_years" not in metrics["periods"]
        disclosure = data["benchmark_disclosures"][0]
        assert disclosure["inception_date"] == "2015-06-29"
        assert disclosure["selected_share_class_start_date"] == "2024-05-28"
        assert disclosure["definition"].endswith("×45%")
        assert not disclosure["tracking_risk"] and "target_etf" not in data
        assert (
            disclosure["periods"][-1]["nav_reconciliation"]["start_date"]
            == "2024-05-28"
        )
        return AIMessage(content=fund_report("021528", "mixed"))

    run, model, seen = fund_case(
        [research, synthesis],
        code="021528",
        kind="mixed",
        requested_class="C",
        actual_class="C",
        report_text=text,
        nav_rows=rows,
    )
    result = run.run()
    assert result["status"] == "generated" and result["quality_accepted"] is None
    assert result["errors"][0]["stage"] == "research" and model.i == 2
    assert (run.directory / "report.md").exists() and (
        run.directory / "nav-021528.json"
    ).exists()
    saved = json.loads((run.directory / "fund_data.json").read_text(encoding="utf-8"))
    assert (
        saved["benchmark_disclosures"][0]["selected_share_class_start_date"]
        == "2024-05-28"
    )
    assert not any("sec.gov" in url for url in seen)


def test_mixed_performance_tables_do_not_pair_different_cutoffs(fund_case):
    rows = [
        {
            "x": int(
                datetime.fromisoformat(day).replace(tzinfo=CHINA).timestamp() * 1000
            ),
            "y": nav,
            "equityReturn": None,
            "unitMoney": "",
        }
        for day, nav in [
            ("2024-05-28", 1),
            ("2025-10-08", 1.874),
            ("2026-06-30", 5.698),
            ("2026-10-08", 3.324),
        ]
    ]
    run, _, _ = fund_case(
        [],
        code="021528",
        kind="mixed",
        requested_class="C",
        actual_class="C",
        nav_rows=rows,
    )
    tools = run.source_tools
    tools.get_fund_profile()
    tools.get_nav_history()
    url = "https://pdf.dfcfw.com/pdf/H2_AN202608311828748047_1.pdf"
    tools.fund_data["benchmark_disclosures"] = [
        {
            "share_class": "C",
            "report_end_date": "2026-06-30",
            "definition": "股指55%+债指45%",
            "source_url": url,
            "periods": [
                {
                    "period": "过去一年",
                    "fund_return_pct": 445.79,
                    "benchmark_return_pct": 14.78,
                    "return_difference_pp": 431.01,
                    "nav_reconciliation": {"start_date": "2025-06-30"},
                }
            ],
        }
    ]
    bundle = load_skill(kind="fund", fund_type="mixed")
    heading = f"## 6. {bundle.report_headings[5]}"
    bad = "| 指标 | 基金（C类） | 官方基准 |\n|---|---|---|\n| 近1年收益 | 77.37% | 14.78% |\n"
    markdown = fund_report("021528", "mixed").replace(heading, heading + "\n\n" + bad)
    output = insert_mixed_performance(
        markdown, tools.fund_data, "021528", "C", bundle.report_headings
    )
    assert bad not in output
    assert "| 近1年收益 | 77.3746% | 2025-10-08→2026-10-08 |" in output
    assert "445.7900% | 14.7800% | 431.0100" in output
    assert "过去一年（2025-06-30→2026-06-30）" in output
    assert "| 近3年收益 | N.A." in output
    assert output.count("### 所选份额绩效与基准口径") == 1
    assert (
        insert_mixed_performance(
            output, tools.fund_data, "021528", "C", bundle.report_headings
        )
        == output
    )
    boundary = f"## 7. {bundle.report_headings[6]}"
    assert output.split(boundary, 1)[1] == markdown.split(boundary, 1)[1]
    assert not check_report(output, bundle.report_headings, tools.allowed_urls | {url})[
        "errors"
    ]
