"""Normalize Markdown and check report structure against already-read URLs."""

import html
import re
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit


def canonical_url(value):
    """Normalize link syntax and verified aliases without guessing other pages."""
    value = html.unescape(value.strip().strip("<>"))
    parsed = urlsplit(value)
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def _citation_targets(markdown):
    """Collect Markdown and HTML links without truncating balanced URL parentheses."""
    targets = []
    masked = list(markdown)
    for match in re.finditer(r"\]\(\s*", markdown):
        start = cursor = match.end()
        if cursor < len(markdown) and markdown[cursor] == "<":
            start += 1
            cursor = markdown.find(">", start)
            if cursor < 0:
                continue
        else:
            depth = 0
            while cursor < len(markdown):
                character = markdown[cursor]
                if character.isspace() or character == ")" and depth == 0:
                    break
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                cursor += 1
        targets.append(markdown[start:cursor])
        masked[start:cursor] = " " * (cursor - start)
    targets += re.findall(
        r"(?:https?:)?//[^\s<>\"'\[\]，。、；（）]+", "".join(masked), re.I
    )
    targets += re.findall(r"(?m)^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)", markdown)
    for match in re.finditer(
        r"\b(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", markdown, re.I
    ):
        targets.append(next(value for value in match.groups() if value is not None))
    clean = set()
    for target in targets:
        target = target.strip().strip("<>").rstrip("。，；,;")
        while target.endswith(")") and target.count(")") > target.count("("):
            target = target[:-1]
        if not target.startswith("#"):
            clean.add(target)
    return clean


def normalize_markdown(markdown, headings):
    """Normalize only the enclosing fence and known report headings."""
    markdown = markdown.strip()
    if re.match(r"^```(?:markdown|md)?\s*\n", markdown) and markdown.endswith("```"):
        markdown = markdown.split("\n", 1)[1][:-3].strip()
    for index, heading in enumerate(headings, 1):
        pattern = rf"(?m)^\s{{0,3}}#{{1,3}}\s*(?:\d+[.．、:：]\s*)?{re.escape(heading)}\s*#*\s*$"
        markdown = re.sub(pattern, f"## {index}. {heading}", markdown)
    return markdown + "\n" if markdown else ""


def check_report(markdown, headings, allowed_urls):
    """Check delivery structure and read URLs, leaving content quality to acceptance."""
    errors, warnings = [], []
    if not markdown.strip():
        errors.append("report_empty")
    matches = list(re.finditer(r"(?m)^##\s+(.+)$", markdown))
    found = {
        match.group(1).strip(): (position, match)
        for position, match in enumerate(matches)
    }
    positions = []
    for index, heading in enumerate(headings, 1):
        name = f"{index}. {heading}"
        if name not in found:
            errors.append(f"section_missing:{name}")
            continue
        position, match = found[name]
        positions.append(position)
        end = (
            matches[position + 1].start()
            if position + 1 < len(matches)
            else len(markdown)
        )
        body = markdown[match.end() : end].strip()
        if not body:
            errors.append(f"section_empty:{name}")
        if index in {5, 6} and re.search(r"\d", body):
            if not re.search(
                r"USD|CNY|美元|人民币|百万|十亿|亿元|%|％|股|倍|元", body, re.I
            ):
                warnings.append(f"number_unit_review:{name}")
            if not re.search(r"20\d{2}|FY|LTM|TTM|截至|季度|财年", body, re.I):
                warnings.append(f"number_period_review:{name}")
    if positions != sorted(positions) or len(matches) != len(headings):
        errors.append("section_order_or_count")
    allowed = {canonical_url(url) for url in allowed_urls}
    unknown = [
        url for url in _citation_targets(markdown) if canonical_url(url) not in allowed
    ]
    if unknown:
        errors.append("unread_citation")
    return {
        "errors": list(dict.fromkeys(errors)),
        "warnings": warnings,
        "unread_urls": sorted(set(unknown)),
    }


def _source_list(sources):
    lines = ["### 本次实际读取的来源", ""]
    seen = set()
    for item in sources:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        title = (
            str(item["title"])
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("\n", " ")
        )
        try:
            retrieved = datetime.fromisoformat(
                str(item.get("retrieved_at", "")).replace("Z", "+00:00")
            )
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=UTC)
            read_date = (
                retrieved.astimezone(timezone(timedelta(hours=8))).date().isoformat()
            )
        except (ValueError, TypeError):
            read_date = "未确认"
        lines.append(
            f"- [{title}](<{url}>)；发布日期：{item.get('published_at') or '未确认'}；读取日期：{read_date}。"
        )
    return "\n".join(lines) + "\n"


def insert_fund_costs(markdown, fund_data, headings):
    """Place the reproducible A/C scenario table inside the fund-quality section."""
    costs = (fund_data or {}).get("share_class_costs")
    if not costs:
        return markdown
    title = "### A/C费用情景与复算口径"
    lines = [
        title,
        "",
        f"A类销售服务费 {costs['A_sales_service_pct_per_year']:g}%/年；"
        f"C类 {costs['C_sales_service_pct_per_year']:g}%/年；"
        f"费用差 {costs['annual_fee_difference_pp']:g} 个百分点/年。",
        "",
        "| A类一次性申购费假设 | 约略平衡年数 | 假设说明 |",
        "|---|---:|---|",
    ]
    for index, item in enumerate(costs["scenarios"]):
        assumption = (
            "已读取资料中的最高申购费；不是用户实际渠道报价"
            if index == 0
            else "按最高费率一折作假设；不是已确认的用户折扣"
        )
        lines.append(
            f"| {item['assumed_A_subscription_pct']:g}% | {item['break_even_years']:g} 年 | {assumption} |"
        )
    lines += [
        "",
        "复算公式：A类一次性申购费率 ÷（C类年销售服务费率 − A类年销售服务费率）。",
        "这是仅比较申购费和销售服务费的近似情景，未计赎回费差异、复利及其他费用；用户渠道和持有期未知，不能据此直接宣布哪类份额更优。",
    ]
    sources = list(
        dict.fromkeys(
            [item["url"] for item in costs.get("dated_fee_sources", [])]
            + costs.get("source_urls", [])
        )
    )
    lines += [
        "费用依据："
        + "、".join(f"[资料{i}]({url})" for i, url in enumerate(sources, 1))
        + "。",
        "",
    ]
    # A subsection remains ordinary Markdown; it adds no primary report section or schema.
    heading = f"## 6. {headings[5]}"
    if heading not in markdown:
        return markdown
    markdown = re.sub(
        rf"(?ms)^{re.escape(title)}\n.*?(?=^#{{2,3}}\s|\Z)",
        "",
        markdown,
    )
    position = markdown.index(heading)
    return (
        markdown[:position].rstrip()
        + "\n\n"
        + "\n".join(lines).rstrip()
        + "\n\n"
        + markdown[position:]
    )
