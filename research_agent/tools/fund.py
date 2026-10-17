"""Acquire passive-fund materials and calculate NAV metrics within the MVP budget."""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from langchain_core.tools import StructuredTool

from research_agent.config import ResearchError
from research_agent.runtime import _write_json
from research_agent.tools.fund_data import (
    CHINA,
    fund_code,
    parse_documents,
    parse_nav,
    parse_profile,
    share_class,
)
from research_agent.tools.fund_metrics import compare_nav, summarize_nav
from research_agent.tools.mvp import MvpTools
from research_agent.tools.pdf import pdf_window, read_pdf

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
_DATA_TYPES = ("application/javascript", "text/javascript", "text/plain")


class FundTools(MvpTools):
    """Reuse source, note and transport controls for one explicitly selected fund."""

    def __init__(self, *args, fund_type, share_class, **kwargs):
        """Bind the selected subtype and share class without selecting another agent."""
        super().__init__(*args, **kwargs)
        self.fund_type, self.share_class = fund_type, share_class
        self.fund_data = {
            "identity_verified": False,
            "profiles": {},
            "nav_metrics": {},
            "comparisons": {},
            "gaps": [],
        }
        self._profiles, self._series, self._pdfs = {}, {}, {}
        self._documents = {}
        self._dated_fees = {"A": {}, "C": {}}
        self._fee_sources = []

    def _source(self, page, title, text, **extra):
        result = {
            "requested_url": page.url,
            "url": page.url,
            "title": title,
            "published_at": None,
            "retrieved_at": datetime.now(CHINA).isoformat(),
            "text": text,
            "truncated": False,
            **extra,
        }
        self._remember_source(result)
        return result

    def get_fund_profile(self, code: str = "") -> dict:
        """Read identity, benchmark and dated profile fields for a fund or comparable ETF."""

        def operation():
            selected = fund_code(code or self.ticker)
            if selected in self._profiles:
                return self._profiles[selected]
            page = self.fetcher.get(
                f"https://fundf10.eastmoney.com/jbgk_{selected}.html",
                headers=_HEADERS,
            )
            profile = parse_profile(page.text, selected)
            if selected == self.ticker and (
                profile["fund_type"] != self.fund_type
                or profile["share_class"] != self.share_class
            ):
                raise ResearchError(
                    "fund_identity_mismatch", "Fund subtype or share class differs."
                )
            if profile["fund_type"] not in {"passive_index", "etf_feeder", "etf"}:
                raise ResearchError(
                    "fund_type_unsupported", "Unsupported fund product."
                )
            profile["source_url"] = page.url
            profile["snapshot_date"] = datetime.now(CHINA).date().isoformat()
            if self.as_of < datetime.now(CHINA).date():
                profile.pop("fees_percent", None)
                profile.pop("raw_profile_fields", None)
                profile["historical_gap"] = (
                    "Current identity snapshot only; historical fees, manager, size and mandate "
                    "require disclosures published before the cutoff."
                )
            self._source(
                page,
                f"{profile['name']}：基金档案（数据商快照）",
                json.dumps(profile, ensure_ascii=False),
                extraction="named profile fields; current snapshot is not historical evidence",
            )
            self._profiles[selected] = profile
            self.fund_data["profiles"][selected] = profile
            return profile

        return self._invoke("get_fund_profile", operation, acquire=True)

    def get_nav_history(self, code: str = "", comparison_code: str = "") -> dict:
        """Calculate dated returns and risk from saved NAV rows; optionally align a comparison."""

        def load(selected):
            if selected in self._series:
                return self._series[selected]
            page = self.fetcher.get(
                f"https://fund.eastmoney.com/pingzhongdata/{selected}.js",
                headers=_HEADERS,
                content_types=_DATA_TYPES,
            )
            name, rows, raw = parse_nav(page.text, selected, self.as_of)
            if selected == self.ticker:
                profile = self._profiles.get(selected)
                if not profile or share_class(name) != self.share_class:
                    raise ResearchError(
                        "fund_identity_unverified", "Verify the profile before NAV."
                    )
                if re.sub(r"\s+|发起式", "", name) != re.sub(
                    r"\s+|发起式", "", profile["name"]
                ):
                    raise ResearchError(
                        "fund_identity_mismatch", "The NAV fund name differs."
                    )
            summary = summarize_nav(rows)
            filename = f"nav-{selected}.json"
            _write_json(
                self.directory / filename,
                {
                    "code": selected,
                    "name": name,
                    "source_url": page.url,
                    "as_of": str(self.as_of),
                    "timestamp_timezone": "UTC+08:00",
                    "raw_rows": raw,
                },
            )
            summary.update(
                code=selected,
                name=name,
                source_url=page.url,
                data_file=filename,
                requested_cutoff=str(self.as_of),
                first_available_date=rows[0]["date"],
                nav_lag_calendar_days=(
                    self.as_of - date.fromisoformat(rows[-1]["date"])
                ).days,
            )
            if summary["nav_lag_calendar_days"] > 10:
                summary["gaps"].append(
                    "The latest available NAV is more than ten calendar days before cutoff."
                )
            self._source(
                page,
                f"{name}：净值序列",
                json.dumps(
                    {
                        "code": selected,
                        "name": name,
                        "observations": len(rows),
                        "first_observation": rows[0],
                        "last_observation": rows[-1],
                        "cutoff": str(self.as_of),
                        "raw_data_file": filename,
                        "note": "Only observations on or before cutoff are retained; computed measures are in fund_data.",
                    },
                    ensure_ascii=False,
                ),
                effective_date=rows[-1]["date"],
                extraction="parsed dated NAV observations; provider JavaScript was not executed",
            )
            self._series[selected] = rows
            self.fund_data["nav_metrics"][selected] = summary
            self._append_note(
                f"### 程序计算：{name}（{selected}）\n\n"
                + json.dumps(summary, ensure_ascii=False)
                + f"\n\n来源：{page.url}"
            )
            if selected == self.ticker:
                self.fund_data["identity_verified"] = True
            return rows

        def operation():
            selected = fund_code(code or self.ticker)
            rows = load(selected)
            result = {"metrics": self.fund_data["nav_metrics"][selected]}
            if comparison_code:
                other = fund_code(comparison_code)
                compared = compare_nav(rows, load(other))
                compared.update(fund_code=selected, comparison_code=other)
                self.fund_data["comparisons"][f"{selected}/{other}"] = compared
                self._append_note(
                    "### 程序计算：同日净值对比\n\n"
                    + json.dumps(compared, ensure_ascii=False)
                )
                result["comparison"] = compared
            return result

        return self._invoke("get_nav_history", operation, acquire=True)

    def get_fund_documents(self, code: str = "", limit: int = 3) -> dict:
        """Discover dated reports and product summaries; PDF URLs become citations only after reading."""

        def operation():
            selected = fund_code(code or self.ticker)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 3
            ):
                raise ResearchError(
                    "invalid_input", "Select one to three disclosure types."
                )
            if selected not in self._documents:
                url = (
                    "https://api.fund.eastmoney.com/f10/JJGG"
                    f"?fundcode={selected}&pageIndex=1&pageSize=50&type=0"
                )
                payload = self.fetcher.get_json(url, headers=_HEADERS)
                records = parse_documents(payload, selected, self.as_of)
                for item in records:
                    self._discovered[item["url"]] = {
                        **item,
                        "published_at": date.fromisoformat(item["published_at"]),
                        "source_type": "fund_disclosure",
                    }
                self._documents[selected] = records
            return {
                "documents": self._documents[selected][:limit],
                "coverage": "discovery_only",
            }

        return self._invoke("get_fund_documents", operation, acquire=True)

    def read_source(
        self, url: str, focus: str = "", page_number: int = 0, max_chars: int = 0
    ) -> dict:
        """Read HTML or text PDF; select focus or a 1-based PDF page_number and bounded max_chars."""
        if max_chars and (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or not 500 <= max_chars <= self.settings.max_content_chars
        ):
            return self._invoke("read_source", lambda: self._invalid_url())
        if not isinstance(url, str):
            return self._invoke("read_source", lambda: self._invalid_url())
        try:
            is_pdf = urlsplit(url).path.lower().endswith(".pdf")
        except ValueError:
            return self._invoke("read_source", lambda: self._invalid_url())
        if not is_pdf:
            if page_number:
                return self._invoke("read_source", lambda: self._invalid_url())
            result = super().read_source(url, focus)
            if max_chars and not result.get("error"):
                return {
                    **result,
                    "text": result["text"][:max_chars],
                    "truncated": result["truncated"] or len(result["text"]) > max_chars,
                }
            return result

        def operation():
            metadata = self._metadata(url)
            published = metadata.get("published_at")
            if published is None:
                found = re.search(r"(?:AN|/)(20\d{6})", url)
                if found:
                    try:
                        published = datetime.strptime(found.group(1), "%Y%m%d").date()
                    except ValueError:
                        pass
            if published is None:
                raise ResearchError(
                    "fund_publication_unverified",
                    "Discover the dated disclosure before reading this PDF.",
                )
            if published > self.as_of:
                raise ResearchError("future_source", "Publication is after cutoff.")
            if url not in self._pdfs:
                page = self.fetcher.get(
                    url,
                    headers=_HEADERS,
                    content_types=("application/pdf", "application/octet-stream"),
                    before_request=self._reject_sec_redirect,
                )
                pages, total = read_pdf(page.body, self.budget)
                text = " ".join(pages)
                bound_code = metadata.get("fund_code")
                if bound_code and not re.search(rf"(?<!\d){bound_code}(?!\d)", text):
                    raise ResearchError(
                        "fund_identity_mismatch",
                        "The disclosure does not identify the bound fund.",
                    )
                for match in re.finditer(
                    r"(?:送出日期|发布日期|公告日期)\s*[：:]\s*(20\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})",
                    text,
                ):
                    try:
                        declared = date(*(int(value) for value in match.groups()))
                    except ValueError:
                        continue
                    if declared > self.as_of:
                        raise ResearchError(
                            "future_source",
                            "The PDF declares publication after cutoff.",
                        )
                self._pdfs[url] = page, pages, total
            page, pages, total = self._pdfs[url]
            window = pdf_window(
                pages,
                total,
                focus=focus,
                max_chars=max_chars or self.settings.max_content_chars,
                start_page=page_number,
            )
            result = self._source(
                page,
                metadata.get("title", "基金披露 PDF"),
                window.pop("text"),
                requested_url=url,
                published_at=published.isoformat(),
                **window,
            )
            if metadata.get("fund_code") == self.ticker and metadata.get("kind") in {
                "periodic",
                "summary",
            }:
                text = " ".join(pages)
                compact = re.sub(r"\s+", "", text)
                c_fee = re.search(
                    r"C类基金份额的销售服务费年费率为(\d+(?:\.\d+)?)%", compact
                )
                if c_fee:
                    self._dated_fees["C"]["sales_service"] = float(c_fee.group(1))
                if re.search(r"A类基金份额[^。]{0,100}不收取销售服务费", compact):
                    self._dated_fees["A"]["sales_service"] = 0
                if metadata.get("kind") == "summary" and self.share_class == "A":
                    position = compact.find("申购费")
                    stop = compact.find("赎回费", position)
                    if position >= 0 and stop > position:
                        rates = re.findall(r"(\d+(?:\.\d+)?)%", compact[position:stop])
                        if rates:
                            self._dated_fees["A"]["maximum_subscription"] = max(
                                float(rate) for rate in rates
                            )
                if any(self._dated_fees.values()):
                    self._fee_sources.append(
                        {"url": page.url, "disclosure_date": published.isoformat()}
                    )
                position = text.find("目标基金基本情况")
                if position >= 0:
                    found = re.search(
                        r"基金主代码\s*(\d{6})", text[position : position + 1800]
                    )
                    if found and found.group(1) != self.ticker:
                        self.fund_data["target_etf"] = {
                            "code": found.group(1),
                            "source_url": page.url,
                            "disclosure_date": published.isoformat(),
                        }
                classes = re.search(
                    r"下属分级基金的交易代码\s+((?:\d{6}\s*){2,6})", text
                )
                if classes:
                    self.fund_data["related_share_codes"] = {
                        "codes": re.findall(r"\d{6}", classes.group(1)),
                        "source_url": page.url,
                        "disclosure_date": published.isoformat(),
                    }
            return result

        return self._invoke("read_source", operation, acquire=True)

    @staticmethod
    def _invalid_url():
        raise ResearchError("invalid_input", "Use a public source URL.")

    def _share_costs(self):
        """Calculate conditional A/C costs from actual profile fees, not return differences."""
        profiles = self.fund_data["profiles"]
        if not profiles[self.ticker].get("full_name"):
            return
        classes = {}
        for profile in profiles.values():
            label = profile.get("share_class")
            if label in {"A", "C"} and profile.get("full_name") == profiles[
                self.ticker
            ].get("full_name"):
                classes[label] = profile
        for code in self.fund_data.get("related_share_codes", {}).get("codes", []):
            if {"A", "C"} <= classes.keys():
                break
            if code == self.ticker:
                continue
            profile = self.get_fund_profile(code)
            if (
                not profile.get("error")
                and profile.get("share_class") in {"A", "C"}
                and profile.get("full_name") == profiles[self.ticker].get("full_name")
            ):
                classes[profile["share_class"]] = profile
        if not {"A", "C"} <= classes.keys():
            return
        a, c = classes["A"], classes["C"]
        af = {**a.get("fees_percent", {}), **self._dated_fees["A"]}
        cf = {**c.get("fees_percent", {}), **self._dated_fees["C"]}
        for profile, dated in ((a, self._dated_fees["A"]), (c, self._dated_fees["C"])):
            current = profile.get("fees_percent", {})
            if any(
                key in current and current[key] != value for key, value in dated.items()
            ):
                self.fund_data["gaps"].append(
                    "Current profile fees conflict with a dated disclosure; A/C cost preference is unresolved."
                )
                return
        if (
            not {"maximum_subscription", "sales_service"} <= af.keys()
            or "sales_service" not in cf
        ):
            return
        difference = Decimal(str(cf["sales_service"])) - Decimal(
            str(af["sales_service"])
        )
        if difference <= 0:
            return
        maximum = Decimal(str(af["maximum_subscription"]))
        self.fund_data["share_class_costs"] = {
            "A_code": a["code"],
            "C_code": c["code"],
            "A_sales_service_pct_per_year": af["sales_service"],
            "C_sales_service_pct_per_year": cf["sales_service"],
            "annual_fee_difference_pp": float(difference),
            "scenarios": [
                {
                    "assumed_A_subscription_pct": float(maximum),
                    "break_even_years": float(maximum / difference),
                    "assumption": "maximum A subscription rate; user's channel fee is unknown",
                },
                {
                    "assumed_A_subscription_pct": float(maximum / 10),
                    "break_even_years": float(maximum / 10 / difference),
                    "assumption": "hypothetical 10%-of-maximum A rate, not the user's actual discount",
                },
            ],
            "formula": "A one-time subscription percent / (C minus A annual sales-service fee percentage points)",
            "scope": "Approximation only; excludes redemption-fee differences, compounding and other charges. Never infer fees from realized return differences.",
            "source_urls": [a["source_url"], c["source_url"]],
            "dated_fee_sources": self._fee_sources,
        }
        self._append_note(
            "### 程序计算：A/C费用情景（非用户实际渠道报价）\n\n"
            + json.dumps(self.fund_data["share_class_costs"], ensure_ascii=False)
        )

    def discover_fund_sources(self):
        """Read core fund materials before spending model calls; retain explicit failures."""
        profile = self.get_fund_profile()
        if profile.get("error"):
            return profile
        history = self.get_nav_history()
        if history.get("error"):
            return history
        if "易方达" in str(profile.get("manager_company", "")):
            official = self.read_source(
                f"https://api.efunds.com.cn/owch/oURL/website/weixin/wx_fundinfo.shtml?fundcode={self.ticker}"
            )
            if official.get("error"):
                self.fund_data["gaps"].append({"official_profile": official["error"]})
        disclosures = self.get_fund_documents()
        for item in disclosures.get("documents", [])[:2]:
            result = self.read_source(item["url"])
            if result.get("error"):
                self.fund_data["gaps"].append(
                    {"document": item["title"], "error": result["error"]}
                )
            elif item["kind"] == "periodic":
                self.read_source(item["url"], focus="销售服务费", max_chars=6000)
                if self.fund_type == "passive_index":
                    self.read_source(item["url"], focus="投资组合报告", max_chars=16000)
        self._share_costs()
        target = self.fund_data.get("target_etf", {}).get("code")
        if target:
            comparison = self.get_nav_history(comparison_code=target)
            if comparison.get("error"):
                self.fund_data["gaps"].append({"target_etf_nav": comparison["error"]})
            documents = self.get_fund_documents(target)
            periodic = next(
                (d for d in documents.get("documents", []) if d["kind"] == "periodic"),
                None,
            )
            if periodic:
                result = self.read_source(
                    periodic["url"], focus="投资组合报告", max_chars=16000
                )
                if result.get("error"):
                    self.fund_data["gaps"].append(
                        {"target_etf_disclosure": result["error"]}
                    )
        elif self.fund_type == "etf_feeder":
            self.fund_data["gaps"].append(
                "Target ETF code was not verified from a read disclosure."
            )
        return {
            "fund_data": self.fund_data,
            "read_sources": self.sources,
            "discovered_documents": disclosures.get("documents", []),
        }

    def tools(self):
        """Expose fund data and shared reading/arithmetic tools, without SEC discovery."""
        return [
            StructuredTool.from_function(method)
            for method in (
                self.get_fund_profile,
                self.get_nav_history,
                self.get_fund_documents,
                self.search_sources,
                self.read_source,
                self.calculate,
                self.save_notes,
            )
        ]
