"""Select materials and execution focus inside the shared fund MVP."""

FUND_TYPES = {
    "passive_index": {
        "periodic_readings": (
            ("销售服务费", 6000),
            ("投资组合报告", 16000),
        ),
        "research_focus": (
            "Read the tracked index and the fund's own portfolio. Prioritize dated index "
            "methodology, holdings, valuation and same-index alternatives."
        ),
        "synthesis_focus": (
            "Explain index exposure and passive implementation. A comparison fund is "
            "secondary context, not the official benchmark."
        ),
    },
    "etf_feeder": {
        "periodic_readings": (("销售服务费", 6000),),
        "research_focus": (
            "Read the target ETF and underlying index for look-through holdings; a feeder's "
            "small direct stock allocation is not its economic equity exposure. Verify any "
            "fee exclusion on target-ETF holdings from the actual contract."
        ),
        "synthesis_focus": (
            "Explain the feeder, verified target ETF and index separately. Do not double-count "
            "fees where the contract excludes charges on target-ETF holdings."
        ),
    },
    "enhanced_index": {
        "periodic_readings": (
            ("基金净值表现", 11000),
            ("报告期内基金的投资策略和业绩表现", 8000),
            ("投资组合报告", 16000),
            ("销售服务费", 6000),
        ),
        "research_focus": (
            "Read the actual enhancement process, manager tenure, portfolio deviations, "
            "turnover/costs and official benchmark performance table. Do not assume that "
            "enhancement is quantitative or that this fund holds a target ETF. Examine the "
            "disclosed tracking-error target and actual result, including any target exceedance. "
            "Use dated official period returns when benchmark daily data is unavailable."
        ),
        "synthesis_focus": (
            "Assess the index exposure and enhancement implementation separately. Use "
            "benchmark_disclosures for official same-period return differences and risk facts. "
            "A disclosed target_exceeded=true is material counter-evidence: show actual tracking "
            "error, target and reporting period in the conclusion and risk section. A target "
            "exceedance alone is not a legal compliance finding. Do not infer information ratio "
            "or tracking error from return-table standard-deviation columns. Net NAV returns "
            "already reflect fund-level operating expenses; do not subtract them again. "
            "Overlapping published windows do not establish independent, persistent alpha. "
            "Manager explanations of excess return are attributed opinions unless verified. "
            "Do not infer a multi-year causal attribution from one holdings snapshot or infer "
            "that a peer never exceeds its target from that target alone. "
            "Do not apply feeder-specific fee exclusions or invent a target ETF."
        ),
    },
    "mixed": {
        "periodic_readings": (
            ("基金净值表现", 10000),
            ("基金经理", 6500),
            ("报告期内基金的投资策略和业绩表现", 8000),
            ("投资组合报告", 14000),
            ("销售服务费", 5000),
        ),
        "research_focus": (
            "Read the mixed fund's contractual stock/bond/cash limits separately from actual "
            "disclosed allocation. A composite benchmark's weights are not portfolio weights. "
            "Use the current and previous periodic report to examine allocation, concentration "
            "and style changes; a single snapshot cannot establish style stability. Read manager "
            "tenure and actual investment process. Select peers with similar mandates and risk "
            "exposure rather than assuming an index ETF is the default alternative. Verify "
            "the selected share class's start date and never borrow the A class's longer history."
        ),
        "synthesis_focus": (
            "Write a mixed-fund report, adapting section 5 to manager/process, allocation, style, "
            "concentration, capacity and costs. Do not impose an index replication or tracking-error "
            "target, invent a target ETF, or treat mixed as automatically balanced or low risk. "
            "Distinguish contractual allocation ranges, actual allocation, and benchmark weights; "
            "preserve whether percentages use total assets or net assets. Use dated current and "
            "previous holdings, and acknowledge disclosure lag. Attribute manager commentary as "
            "opinion; holdings alone cannot establish multi-year performance causality. "
            "Report performance for the requested share class only: a later-added C class has "
            "its own lifetime; unavailable three-year history stays unavailable. Parent/A-class "
            "history may be explicitly labeled context but never presented as C-class performance. "
            "benchmark_disclosures preserves official same-period benchmark returns, not a daily "
            "benchmark series. Peer differences are not manager alpha or the official benchmark. "
            "For section 8, use sourced contractual terms or directional research conditions; "
            "do not manufacture allocation, drawdown, concentration or purchase thresholds."
            " The application fills the canonical performance tables in section 6 from fund_data, "
            "separating latest NAV metrics and official same-period benchmark returns; explain "
            "their implications without comparing different dates or duplicating those tables."
        ),
    },
    "bond": {
        "periodic_readings": (
            ("基金净值表现", 10000),
            ("基金经理", 6000),
            ("报告期内基金的投资策略和业绩表现", 6500),
            ("投资组合报告", 12000),
            ("信用风险", 8500),
            ("市场风险", 12000),
            ("销售服务费", 5000),
        ),
        "research_focus": (
            "Read the bond fund's dated mandate first: pure bonds versus permitted stock, "
            "convertible/exchangeable bond and ABS exposure. Separate contractual limits from "
            "actual current and previous holdings. Examine rates/credit mix, credit ratings "
            "and unrated explanations, issuer concentration, subordinated bank capital/perpetual "
            "debt, repo funding, liquidity/redemption terms and manager tenure. Read disclosed "
            "interest-rate sensitivity and balance-sheet risk notes, not only the top-five bonds. "
            "Keep total-assets and net-assets denominators distinct; bond weight above 100% "
            "of NAV is possible and is not itself a breach. Use calculate for ratios from "
            "disclosed figures with the same date and scope. If duration, yield-to-maturity, "
            "credit spreads or complete rating data are missing, retain gaps; do not infer "
            "them from maturity buckets, NAV volatility or a few holdings. Reconcile the "
            "requested share class's distributions, official period returns and dated fee terms. "
            "Conflicting fee disclosures remain a gap until a dated change notice resolves them. "
            "Choose comparable bond mandates/duration/credit budgets for peers; an equity ETF "
            "is not a default substitute. Do not add a target ETF or index tracking-error target."
            " A single bond's weight is not aggregate issuer exposure: sum all bonds from "
            "that issuer or retain the issuer-concentration gap. A purchase cap constrains "
            "daily execution but neither prohibits all purchases nor proves a capacity problem. "
            "Rate/credit conditions described in an old periodic report are historical context; "
            "obtain dated current evidence or leave current market positioning unresolved."
        ),
        "synthesis_focus": (
            "Write a bond-fund report: coupon/carry, interest-rate moves, credit spread changes "
            "and repo financing are potential return drivers, not a verified attribution of "
            "historical excess return. A bond fund is not a deposit or a capital-guaranteed product. "
            "Explain interest-rate, credit/default, subordination/non-redemption, leverage, "
            "funding and redemption risks using read evidence. AAA does not mean risk-free; "
            "unrated does not automatically mean poor credit. Keep policy bank debt distinct "
            "from commercial-bank capital bonds. Preserve dates, portfolio/share-class scope "
            "and total-assets versus NAV denominators. If deriving gross assets/NAV or repo/NAV, "
            "show the same-date inputs and calculation; do not call either portfolio duration. "
            "An estimate derived from fund-NAV interest-rate sensitivity is an approximate "
            "fund-level sensitivity, not an officially disclosed bond-portfolio duration. "
            "It already reflects the fund's asset/liability exposures; do not multiply it by "
            "leverage again. State this limitation wherever using such an estimate. "
            "An official rate-shock scenario is conditional, not a forecast or guaranteed loss cap; "
            "do not extrapolate it to different shocks without a model. Missing duration, YTM "
            "or current spreads stay gaps. Historical return/annualization is not current carry "
            "or expected future yield, and a low unit NAV is not cheapness. Report only the "
            "selected share class's history and disclose the distribution-event approximation. "
            "The application fills section 6 performance tables, separating latest NAV metrics "
            "from dated official same-period benchmark returns; explain rather than duplicate "
            "them. No equity DCF/PE, invented target ETF or tracking-error target. Dated fee "
            "conflicts prohibit a definitive A/C preference or an invented break-even horizon. "
            "Use sourced redemption rules and directional reassessment conditions; do not "
            "invent rate, NAV, drawdown or holding-period purchase thresholds."
            " Never label the largest single-bond weight as the largest issuer weight without "
            "aggregating that issuer's other bonds. A daily purchase cap is a transaction limit; "
            "without the user's planned amount it does not make buying universally impossible "
            "or prove that fund capacity caused the restriction. Market commentary dated to "
            "an earlier report must not become a claim about rates/spreads at the cutoff. "
            "If current timing evidence is missing, state that gap and keep the conclusion conditional."
        ),
    },
}

SUPPORTED_FUND_TYPES = tuple(FUND_TYPES)
