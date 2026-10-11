# Source Verification

## Evidence Standard

Treat price, NAV, valuation, performance, financials, holdings, fees, manager, fund size, index constituents, policy, news, flows, and sector conditions as time-sensitive. Do not describe them as current unless verified for the report's information cutoff.

Before forming the conclusion, create a compact evidence ledger with:

| Field | Requirement |
|---|---|
| Claim | The exact fact needed by the investment judgment |
| Value | Number or concise factual statement |
| Effective date | Market date, report period, or disclosure date |
| Definition | Unit, calculation method, comparison base, and relevant exclusions |
| Source | Direct Markdown link and source tier |
| Status | Verified, source opinion, research inference, or unresolved |

Use one ledger row as the canonical version of a decisive metric. Reconcile conflicts before drafting.

## Source Priority

Prefer sources in this order:

1. Official filings, exchange disclosures, company or fund-company pages, fund contracts, prospectuses, product summaries, periodic reports, and index methodology documents.
2. Regulators, index providers, official statistics, exchanges, and clearing data.
3. Reputable market-data providers and financial media.
4. Secondary commentary as context only.

Use primary sources for static product facts and decisive financial facts whenever available. If only a dynamic market-data page is available, state the retrieval date. If sources conflict, explain the conflict, prefer the primary source when appropriate, and do not silently choose the number that best supports the thesis.

## Fact, Opinion, Inference, And Judgment

Keep these categories distinct:

- **Verified fact:** directly supported by a cited source.
- **Source opinion:** attributed to the named institution or speaker; never converted into established fact.
- **Research inference:** a transparent interpretation of one or more verified facts.
- **Final judgment:** the research conclusion, with its assumptions and invalidation conditions.

Do not use causal language such as “X drove the market” merely because an article mentions X on the same day. Write “the source attributed the move partly to X” unless causality is independently demonstrated.

## Metric Discipline

- Give every changing figure an effective date and describe it as intraday, close, latest NAV, reporting-period data, or latest public disclosure.
- For a stock price, state exchange, currency, close or intraday status, and trading date. For market value, state whether it is provider-reported or calculated and identify the ordinary, basic, diluted, free-float, ADR, or ADS share-count basis.
- For stock financials, keep reporting currency, fiscal period, accounting standard, continuing operations, and per-share denominator consistent. Label actuals `A` and estimates `E`; reconcile adjusted or non-GAAP figures to the statutory measure when they affect the conclusion.
- For returns, state the start/end basis and compare like with like. For funds, prioritize the official benchmark and target ETF over peers or broad indexes.
- For volatility, tracking error, drawdown, and standard deviation, state the period and definition or frequency when available.
- For PE, PB, PS, EV/EBITDA, yield, and valuation percentiles, state the aggregation method, trailing/forward period, currency, negative-earnings treatment when known, and history window. Do not combine a value from one method with a percentile from another.
- For stock valuation, show enough of the bridge to reproduce the result: forecast driver, earnings or cash-flow measure, multiple or discount assumptions, net debt and other enterprise-to-equity adjustments, share count, currency or FX conversion, and valuation date.
- For a technical support level, identify the price-series cutoff, adjustment convention, interval, repeatable method, and failure condition. A round number, one price touch, or chart impression is not verified support.
- For repeated metrics, keep the same date, definition, and number. If two valid definitions are useful, label and interpret both separately.
- For derived figures, show the formula or enough inputs to reproduce the result.

## Citation Rules

Keep citations next to the fact they support. A paragraph containing several decisive facts may need more than one citation. A general link at the end of a long section is not enough.

For dynamic pages, include the information cutoff or retrieval date in the sentence. For PDFs or periodic reports, identify the reporting period. Link to the underlying document rather than a search result.

## Missing Evidence

If a decisive data point cannot be verified:

- Mark it `未核实` in the evidence ledger and list it under `未核实或证据不足的信息`.
- State what source or disclosure would resolve the gap.
- Do not use it as a decisive reason or precise entry threshold.
- Downgrade the evidence status to `部分缺口` or `不足`.

## Final Quality Gates

Before saving the report, confirm all of the following:

1. Each decisive reason maps to verified evidence and a clear implication.
2. No repeated metric has an unexplained date, definition, or value conflict.
3. Product or asset quality, current timing, and personal suitability are not conflated.
4. Exact thresholds are derived and falsifiable; unsupported precision has been removed.
5. Material counter-evidence and thesis invalidation conditions are visible.
6. Every section adds decision value; repetition and non-decisive data have been removed.
7. The conclusion would change if its stated key conditions changed.
8. The report presents sourced basic information, sector/theme classification, recent sector status, material risks, and follow-up indicators in the locations required by the selected stock or fund template.
9. Each follow-up indicator includes a baseline or explicit data gap, a direction or trigger, an update source or frequency, and its decision relevance.
10. A stock report uses the nine required primary sections, and its conclusion, business model, industry state, competitive evidence, financial forecast, valuation, risks, and execution plan do not contradict one another.
11. A stock report's fair-value range, upside/downside, and any technical support level are reproducible from disclosed dates, definitions, inputs, and assumptions, or the report explicitly states the evidence gap instead of inventing precision.
12. A fund report uses the nine required primary sections, and its conclusion, exposure, profit drivers, vehicle quality, risk-return evidence, alternatives, and action plan do not contradict one another.
