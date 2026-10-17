"""Calculate dated fund risk and tracking measures from retained NAV observations."""

import math
from bisect import bisect_right
from datetime import date
from statistics import stdev

from research_agent.config import ResearchError


def _percent(value):
    return round(value * 100, 6)


def _year_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def wealth_series(rows):
    """Build reinvested wealth without treating cumulative NAV as total return."""
    wealth, approximate_events = [], []
    previous = None
    value = 1.0
    for row in rows:
        nav = row["nav"]
        if previous is not None:
            if row["event"]:
                growth = row["daily_return_pct"]
                if growth is None:
                    raise ResearchError(
                        "fund_return_unavailable",
                        "A distribution or split has no published daily NAV return.",
                    )
                gross = 1 + growth / 100
                approximate_events.append(row["date"])
            else:
                gross = nav / previous
                growth = row["daily_return_pct"]
                consecutive = (
                    date.fromisoformat(row["date"]) - date.fromisoformat(wealth[-1][0])
                ).days == 1
                if (
                    consecutive
                    and growth is not None
                    and abs(gross - 1 - growth / 100) > 0.002
                ):
                    raise ResearchError(
                        "fund_return_conflict",
                        "NAV movement conflicts with the published daily return.",
                    )
            if not math.isfinite(gross) or gross <= 0:
                raise ResearchError("fund_nav_invalid", "Invalid NAV return factor.")
            value *= gross
            if not math.isfinite(value) or value <= 0:
                raise ResearchError(
                    "fund_nav_invalid", "Accumulated return is not finite."
                )
        wealth.append((row["date"], value))
        previous = nav
    return wealth, approximate_events


def _window(points):
    first, last = points[0], points[-1]
    days = (date.fromisoformat(last[0]) - date.fromisoformat(first[0])).days
    if len(points) < 2 or days <= 0:
        raise ResearchError("fund_history_short", "At least two dated NAVs are needed.")
    rates = [right[1] / left[1] - 1 for left, right in zip(points, points[1:])]
    peak, peak_day = first[1], first[0]
    worst, drawdown_peak, trough = 0.0, first[0], first[0]
    recovery_level = peak
    for day, value in points:
        if value > peak:
            peak, peak_day = value, day
        drawdown = value / peak - 1
        if drawdown < worst:
            worst, drawdown_peak, trough = drawdown, peak_day, day
            recovery_level = peak
    recovery = next(
        (day for day, value in points if day >= trough and value >= recovery_level),
        None,
    )
    ratio = last[1] / first[1]
    max_gap = max(
        (date.fromisoformat(b[0]) - date.fromisoformat(a[0])).days
        for a, b in zip(points, points[1:])
    )
    return {
        "start_date": first[0],
        "end_date": last[0],
        "observations": len(points),
        "return_pct": _percent(ratio - 1),
        "annualized_return_pct": _percent(ratio ** (365.25 / days) - 1)
        if days >= 365
        else None,
        "max_drawdown_pct": _percent(worst),
        "drawdown_peak_date": drawdown_peak,
        "drawdown_trough_date": trough,
        "recovery_date": recovery if worst else None,
        "annualized_volatility_pct": _percent(stdev(rates) * math.sqrt(252))
        if len(rates) >= 2 and max_gap <= 18
        else None,
        "longest_observation_gap_days": max_gap,
    }


def summarize_nav(rows):
    """Return calendar-window returns, drawdowns and explicitly defined volatility."""
    if len(rows) < 2:
        raise ResearchError("fund_history_short", "At least two dated NAVs are needed.")
    points, approximate_events = wealth_series(rows)
    dates = [item[0] for item in points]
    end = date.fromisoformat(dates[-1])
    periods = {"available_history": _window(points)}
    gaps = []
    for label, years in (("one_year", 1), ("three_years", 3)):
        wanted = _year_before(end, years)
        position = bisect_right(dates, wanted.isoformat()) - 1
        if position < 0 or (wanted - date.fromisoformat(dates[position])).days > 18:
            gaps.append(f"{label}: insufficient comparable NAV history")
        else:
            periods[label] = _window(points[position:])
    if approximate_events:
        gaps.append(
            "Distribution/split days use provider-published daily NAV returns; "
            "cash distribution amounts were not independently reconstructed."
        )
    return {
        "latest_nav_date": rows[-1]["date"],
        "latest_unit_nav": rows[-1]["nav"],
        "periods": periods,
        "method": {
            "return": "wealth_end / wealth_start - 1",
            "wealth": "NAV ratios on ordinary days; provider daily return on distribution/split days",
            "annualized_return": "(wealth_end / wealth_start) ** (365.25 / calendar_days) - 1; only for >=365 days",
            "drawdown": "min(wealth / running_max(wealth) - 1); daily NAV observations",
            "volatility": "sample stdev of observed daily returns * sqrt(252)",
            "dates": "calendar windows; previous available NAV as baseline, no interpolation",
            "sharpe": "not computed: no verified risk-free-rate series",
        },
        "distribution_event_dates": approximate_events,
        "gaps": gaps,
    }


def compare_nav(rows, comparison_rows):
    """Compare aligned total-return observations without renaming an ETF a benchmark."""
    left, _ = wealth_series(rows)
    right, _ = wealth_series(comparison_rows)
    left, right = dict(left), dict(right)
    common = sorted(left.keys() & right.keys())
    if len(common) < 3:
        raise ResearchError(
            "fund_history_short", "Comparable NAV dates are insufficient."
        )
    end = date.fromisoformat(common[-1])
    windows = {"common_history": common}
    for label, years in (("one_year", 1), ("three_years", 3)):
        wanted = _year_before(end, years)
        offset = bisect_right(common, wanted.isoformat()) - 1
        if offset >= 0 and (wanted - date.fromisoformat(common[offset])).days <= 18:
            windows[label] = common[offset:]
    result = {}
    for label, days in windows.items():
        fund_return = left[days[-1]] / left[days[0]] - 1
        other_return = right[days[-1]] / right[days[0]] - 1
        differences = [
            (left[b] / left[a] - 1) - (right[b] / right[a] - 1)
            for a, b in zip(days, days[1:])
        ]
        longest_gap = max(
            (date.fromisoformat(b) - date.fromisoformat(a)).days
            for a, b in zip(days, days[1:])
        )
        result[label] = {
            "start_date": days[0],
            "end_date": days[-1],
            "observations": len(days),
            "fund_return_pct": _percent(fund_return),
            "comparison_return_pct": _percent(other_return),
            "cumulative_difference_pp": _percent(fund_return - other_return),
            "annualized_tracking_error_pct": _percent(
                stdev(differences) * math.sqrt(252)
            )
            if len(differences) >= 2 and longest_gap <= 18
            else None,
        }
    return {
        "periods": result,
        "definition": "cumulative difference is fund return minus comparison return in percentage points; tracking error is sample stdev of aligned return differences * sqrt(252)",
        "comparison_role": "comparison fund/target ETF; not the official composite benchmark",
        "currency_caveat": "Verify matching NAV currencies before interpreting the comparison.",
    }
