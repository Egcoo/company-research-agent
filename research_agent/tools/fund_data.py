"""Parse public fund data without executing provider JavaScript."""

import json
import math
import re
from datetime import UTC, date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

from research_agent.config import ResearchError

CHINA = timezone(timedelta(hours=8))


def fund_code(value):
    """Require an unchanged six-digit fund code, including leading zeros."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}", value):
        raise ResearchError("fund_code_invalid", "Use an explicit six-digit fund code.")
    return value


def js_value(text, name, default=None):
    """Decode one JSON-valued assignment; never evaluate JavaScript."""
    found = re.search(rf"\b(?:var\s+)?{re.escape(name)}\s*=\s*", text)
    if not found:
        return default
    try:
        value, _ = json.JSONDecoder().raw_decode(text[found.end() :])
        return value
    except (ValueError, RecursionError):
        raise ResearchError(
            "fund_data_invalid", "The provider assignment is not JSON."
        ) from None


def share_class(name):
    """Read a terminal share-class label without treating a mainland A-share label as one."""
    base = re.sub(r"[（(][^）)]*[）)]$", "", name.strip())
    if base.upper().endswith(("ETF", "LOF")):
        return None
    found = re.search(
        r"([A-Z])(?:人民币|美元|港币)?(?:[（(][^）)]*[）)])?$", name.strip()
    )
    return found.group(1) if found else None


def parse_profile(html, code):
    """Extract named profile cells and verify the page belongs to the requested fund."""
    fund_code(code)
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for label in soup.select("th"):
        value = label.find_next_sibling("td")
        if value:
            fields[label.get_text("", strip=True)] = value.get_text(" ", strip=True)
    if not re.search(rf"(?<!\d){code}(?!\d)", fields.get("基金代码", "")):
        raise ResearchError(
            "fund_identity_mismatch", "The profile code differs from the request."
        )
    name = fields.get("基金简称", "")
    if not name:
        raise ResearchError(
            "fund_identity_unverified", "No named fund profile was found."
        )
    category = fields.get("基金类型", "")
    identity_name = name + " " + fields.get("基金全称", "")
    kind = (
        "enhanced_index"
        if "增强" in identity_name and ("指数" in identity_name or "指数" in category)
        else "etf_feeder"
        if re.search(r"ETF.*联接|联接.*ETF", name, re.I)
        else "etf"
        if "ETF" in name.upper()
        else "passive_index"
        if "指数" in category and "股票" in category
        else "unsupported"
    )
    fees = {}
    for key, label in (
        ("management", "管理费率"),
        ("custody", "托管费率"),
        ("sales_service", "销售服务费率"),
        ("maximum_subscription", "最高申购费率"),
    ):
        found = re.search(r"(\d+(?:\.\d+)?)\s*%", fields.get(label, ""))
        if found:
            fees[key] = float(found.group(1))
    return {
        "code": code,
        "name": name,
        "full_name": fields.get("基金全称"),
        "fund_type": kind,
        "provider_category": category,
        "share_class": share_class(name),
        "manager_company": fields.get("基金管理人"),
        "benchmark": fields.get("业绩比较基准"),
        "tracked_index": fields.get("跟踪标的"),
        "fees_percent": fees,
        "raw_profile_fields": fields,
        "fee_convention": "Current provider snapshot; maximum subscription fee is not the user's channel fee. Verify historical applicability and feeder fee exclusions from dated disclosures.",
    }


def parse_nav(text, code, as_of):
    """Read dated NAV rows and exclude observations after the requested cutoff."""
    if js_value(text, "fS_code") != fund_code(code):
        raise ResearchError("fund_identity_mismatch", "The NAV-series code differs.")
    name = js_value(text, "fS_name")
    values = js_value(text, "Data_netWorthTrend")
    if (
        not isinstance(name, str)
        or not isinstance(values, list)
        or len(values) > 30_000
    ):
        raise ResearchError("fund_data_invalid", "No bounded NAV series was returned.")
    rows, raw, seen = [], [], set()
    try:
        for item in values:
            stamp = item["x"]
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                raise ValueError("Invalid timestamp")
            day = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(CHINA).date()
            if day > as_of:
                continue
            if isinstance(item["y"], bool) or isinstance(
                item.get("equityReturn"), bool
            ):
                raise ValueError("Boolean NAV or return")
            nav = float(item["y"])
            growth = item.get("equityReturn")
            growth = float(growth) if growth is not None else None
            if (
                not math.isfinite(nav)
                or nav <= 0
                or growth is not None
                and (not math.isfinite(growth) or growth <= -100)
                or day in seen
            ):
                raise ValueError("Invalid or duplicate NAV")
            seen.add(day)
            if not isinstance(item.get("unitMoney", ""), str):
                raise ValueError("Invalid distribution event")
            rows.append(
                {
                    "date": day.isoformat(),
                    "nav": nav,
                    "daily_return_pct": growth,
                    "event": str(item.get("unitMoney") or ""),
                }
            )
            raw.append(item)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise ResearchError(
            "fund_nav_invalid", "The provider NAV observations are invalid."
        ) from None
    rows.sort(key=lambda row: row["date"])
    raw.sort(key=lambda row: row["x"])
    if len(rows) < 2:
        raise ResearchError(
            "fund_history_short", "No usable NAV history before cutoff."
        )
    return name, rows, raw


def parse_documents(payload, code, as_of, limit=4):
    """Select dated research disclosures, leaving their URLs as discovery until read."""
    rows = payload.get("Data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ResearchError("fund_data_invalid", "The disclosure list is invalid.")
    groups = {}
    for item in rows:
        if str(item.get("FUNDCODE")) != code:
            continue
        try:
            published = date.fromisoformat(str(item["PUBLISHDATE"])[:10])
        except (KeyError, ValueError):
            continue
        if published > as_of:
            continue
        title, identity = str(item.get("TITLE", "")), str(item.get("ID", ""))
        if not re.fullmatch(r"AN\d{18}", identity):
            continue
        group = (
            "periodic"
            if re.search(r"(?:年度|中期|季度)报告", title) and "摘要" not in title
            else "summary"
            if "产品资料概要" in title
            else "prospectus"
            if "招募说明书" in title and "提示" not in title
            else None
        )
        if group is None:
            continue
        record = {
            "fund_code": code,
            "title": title,
            "kind": group,
            "published_at": published.isoformat(),
            "url": f"https://pdf.dfcfw.com/pdf/H2_{identity}_1.pdf",
            "coverage": "discovery_only",
        }
        if group not in groups or published.isoformat() > groups[group]["published_at"]:
            groups[group] = record
    return [
        groups[key] for key in ("periodic", "summary", "prospectus") if key in groups
    ][:limit]


def parse_benchmark_disclosure(text, selected_class):
    """Read an explicitly labeled share-class performance table and risk disclosures."""
    result = {"periods": [], "gaps": []}
    for key, pattern in (
        (
            "report_end_date",
            r"报告期末\s*[（(]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        ),
        (
            "inception_date",
            r"基金合同生效日\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        ),
    ):
        found = re.search(pattern, text)
        if found:
            try:
                result[key] = date(
                    *(int(value) for value in found.groups())
                ).isoformat()
            except ValueError:
                pass
    definition = re.search(r"(?m)^业绩比较基准\s*([^\n]+)", text)
    if definition and re.search(r"指数|利率|收益率", definition.group(1)):
        result["definition"] = definition.group(1).strip()
    starts = [
        item
        for item in re.finditer(r"(?m)^3\.2(?:\.1)?[ \t]+基金(?:份额)?净值[^\n]*", text)
        if not re.search(r"\.{4}|…{2}", item.group(0))
    ]
    start = next(
        (item for item in starts if item.group(0).startswith("3.2.1")),
        starts[0] if starts else None,
    )
    if start:
        body = text[start.end() :]
        end = re.search(r"(?m)^3\.2\.2\s|^3\.3\s|^§\s*4\s", body)
        body = body[: end.start()] if end else body
        classes = list(
            re.finditer(r"(?m)^[^\n%]*[\u4e00-\u9fff][^\n%]*?([A-Z])\s*$", body)
        )
        conflicting_periods = set()
        for index, heading in enumerate(classes):
            if heading.group(1) != selected_class:
                continue
            stop = classes[index + 1].start() if index + 1 < len(classes) else len(body)
            section = re.sub(r"\s+", "", body[heading.end() : stop])
            section = section.replace("－", "-").replace("−", "-").replace("％", "%")
            pattern = (
                r"(过去(?:三个月|六个月|一年|三年|五年|七年|十年)|自基金合同生效起至今)"
                + r"([+-]?\d+(?:\.\d+)?)%" * 6
            )
            for found in re.finditer(pattern, section):
                if found.group(1) in conflicting_periods:
                    continue
                values = [float(value) for value in found.groups()[1:]]
                if not all(math.isfinite(value) for value in values):
                    result["gaps"].append(
                        f"{found.group(1)}: non-finite disclosed return"
                    )
                    continue
                difference = round(values[0] - values[2], 6)
                if abs(difference - values[4]) > 0.03:
                    result["gaps"].append(
                        f"{found.group(1)}: inconsistent disclosed return columns"
                    )
                    continue
                item = {
                    "period": found.group(1),
                    "fund_return_pct": values[0],
                    "benchmark_return_pct": values[2],
                    "return_difference_pp": difference,
                }
                previous = next(
                    (p for p in result["periods"] if p["period"] == item["period"]),
                    None,
                )
                if previous is None:
                    result["periods"].append(item)
                elif previous != item:
                    result["gaps"].append(
                        f"{item['period']}: conflicting share-class table rows"
                    )
                    conflicting_periods.add(item["period"])
                    result["periods"] = [
                        row
                        for row in result["periods"]
                        if row["period"] != item["period"]
                    ]
    if "report_end_date" not in result:
        result["gaps"].append(
            "The performance table's report-end date was not verified."
        )
        result["periods"] = []
    if not result["periods"]:
        result["gaps"].append(
            "No unambiguous performance table for the selected share class was parsed."
        )
    compact = re.sub(r"\s+", "", text).replace("％", "%")
    target = re.search(r"年化跟踪误差不超过(\d+(?:\.\d+)?)%", compact)
    actual = {
        float(value)
        for value in re.findall(
            r"年化跟踪误差(?:为|约为|[：:])?(\d+(?:\.\d+)?)%", compact
        )
    }
    risk = {}
    if target:
        risk["tracking_error_target_pct"] = float(target.group(1))
    if len(actual) == 1:
        risk["reported_tracking_error_pct"] = actual.pop()
    elif actual:
        result["gaps"].append(
            "Conflicting reported tracking errors; inspect the original disclosure."
        )
    if {"tracking_error_target_pct", "reported_tracking_error_pct"} <= risk.keys():
        risk["target_exceeded"] = (
            risk["reported_tracking_error_pct"] > risk["tracking_error_target_pct"]
        )
    result.update(
        share_class=selected_class,
        tracking_risk=risk,
        daily_series_available=False,
        information_ratio=None,
        return_scope="Net NAV returns after fund-level operating expenses, before investor subscription/redemption fees.",
        comparison_role="official disclosed benchmark, at the stated report-end date",
        period_caveat="Published windows overlap; positive differences do not establish independent, persistent alpha.",
    )
    result["gaps"].append(
        "Official benchmark daily data is unavailable; information ratio and independently calculated official-benchmark tracking error are not computed. Return-table standard-deviation differences are not tracking error."
    )
    return result
