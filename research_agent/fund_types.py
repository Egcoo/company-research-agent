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
}

SUPPORTED_FUND_TYPES = tuple(FUND_TYPES)
