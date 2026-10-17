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
    kind = (
        "enhanced_index"
        if "增强" in name
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
