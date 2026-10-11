---
name: investment-master
description: Evidence-led Chinese investment research for listed stocks and China OTC public funds, using separate stock and fund workflows and nine-part report templates. Covers stock business models, industry cycles, competitive advantage, management and capital allocation, financial quality, earnings forecasts, valuation, catalysts, risks and entry timing, plus active funds, passive index funds, enhanced index funds, ETF feeder funds and share-class costs. Use when Codex needs to analyze or compare a stock or China mutual fund, write or review a research memo, judge whether the current setup is attractive, or define evidence-based purchase and invalidation conditions.
---

# Investment Master

## Core Rules

Use Chinese unless the user asks otherwise. Write for a financially literate individual reader: keep professional precision, but explain what each decisive metric means for the judgment.

Treat the output as public-information research support, not personalized financial advice. If the user has not provided risk capacity, holding period, position size, and portfolio context, judge only the product or asset and the current setup. State that personal suitability cannot be determined; do not invent a default investor profile.

Create a Markdown research report unless the user explicitly asks for conversation-only output. Create or reuse `reports/` under the current working directory and use:

```text
YYYY-MM-DD-target-name-code.md
```

Use the verified target name and code. If the code remains unknown, omit it from the filename and disclose the gap.

## Required Workflow

1. Identify the target as a listed stock or China OTC public fund.
2. For a fund, classify it before analysis: active, enhanced index, passive index, ETF feeder, bond, mixed, FOF, or other. Confirm the share class separately.
3. Read `references/source-verification.md` before gathering facts.
4. For a stock, read `references/stock-research.md` and `references/memo-template.md`. Keep the stock route independent from fund-specific report rules.
5. For a fund, read `references/china-otc-fund-research.md` and `references/fund-memo-template.md`. For a passive index or ETF feeder fund, also read `references/passive-index-fund-research.md`. Do not apply the stock memo template to a fund.
6. Build a compact evidence ledger before reaching the conclusion. Give every decisive fact one canonical date, definition, value, and source.
7. Verify all time-sensitive facts. Reconcile conflicts and metric definitions before using them.
8. Draft the report, apply the quality gates in `references/source-verification.md`, remove repetition, and save the final Markdown file.

## Stock Output Contract

For a stock, use exactly these nine primary sections in this order unless the user explicitly requests another structure:

1. `投资结论`
2. `公司到底是怎么赚钱的`
3. `所属行业的投资逻辑`
4. `公司竞争力与管理质量`
5. `财务质量与盈利预测`
6. `估值与预期收益`
7. `催化剂、主要风险与逻辑失效条件`
8. `最终购买建议与执行方案`
9. `免责声明和数据来源`

Follow `references/memo-template.md` for the exact content contract. Preserve these decision-critical items inside the nine sections instead of adding extra primary sections:

- Put sourced current price, market value, conclusion label, fair-value range, upside/downside, technical support evidence, horizon, portfolio role, three reasons, and three risk summaries in section 1.
- Put the canonical `标的信息` table and the revenue, profit, customer, pricing, cost, cycle, and cash-flow model in section 2.
- Put `所属板块与主题`, long-term industry logic, and evidence-backed `所属板块近期状态` in section 3. Keep company-specific advantages out of the industry-only discussion.
- Put verified competitive outcomes, management quality, governance, and capital allocation in section 4.
- Put historical accounting and cash-flow quality plus driver-based forecasts in section 5. Label actuals and estimates and expose the assumptions before the forecast values.
- Put suitable absolute and relative valuation methods, current-price implied expectations, three decision-distinct scenarios, fair value, downside, and uncertainty in section 6.
- Put catalysts, consolidated `主要风险`, observable warning signs, invalidation conditions, and an explicit short thesis in section 7.
- Put the final rating, evidence-based purchase method and conditions, portfolio role, horizon, position principle, sell or reassessment conditions, and `后续观察指标` in section 8.
- Put the information cutoff, methodology, major sources, evidence status, unresolved gaps, and disclaimer in section 9. Keep decisive citations close to the claims they support.

Use one stock conclusion label: `买入`, `谨慎买入`, `持有`, `观察`, or `回避`. `持有` is an existing-holder judgment and must not be presented as a new-purchase recommendation. Keep company quality, current valuation or timing, and personal suitability separate.

Treat fair value and technical levels as analytical conclusions, not mandatory numbers to invent. Use an exact fair-value range, target, support level, or purchase threshold only when the method, inputs, effective date, assumptions, and failure conditions are reproducible. Otherwise name the evidence gap and use a directional condition. Never turn an unsupported round-number drawdown or chart impression into a buy rule.

Do not repeat a fact merely because several sections could mention it. Section 1 summarizes the decision; later sections provide the canonical evidence and reasoning.

## Fund Output Contract

For a fund, use exactly these nine primary sections in this order unless the user explicitly requests another structure:

1. `投资结论`
2. `基金到底在投什么`
3. `基金重仓方向的投资逻辑`
4. `为什么可能赚钱`
5. `基金本身是否合格`
6. `收益和风险是否匹配`
7. `当前价格和替代品比较`
8. `最终购买方案`
9. `免责声明和数据来源`

Follow `references/fund-memo-template.md` for the exact content contract. Preserve the earlier decision-critical information inside these nine sections instead of adding extra primary sections:

- Put sourced fund identity, strategy, benchmark, holdings, share class, and real sector/theme exposure in section 2.
- Put the evidence-backed recent state of the dominant sector, style, theme, or asset class in section 3.
- Put the consolidated material-risk analysis in section 6, while section 1 carries only the three most important risk summaries.
- Put purchase conditions, sell or reassessment conditions, and follow-up indicators in section 8.

Use one fund conclusion label: `买入`, `谨慎买入`, `持有`, `观察`, or `回避`. `持有` is an existing-holder judgment and must not be presented as a new-purchase recommendation. Keep exposure quality, vehicle quality, current timing, and personal suitability separate.

Do not add a tenth primary section for an evidence appendix. Keep citations close to decisive claims, and put the information cutoff, source summary, methodology, evidence status, and unresolved gaps in section 9.

Use an exact valuation, NAV, yield, spread, rate, price, or drawdown threshold only when its calculation, assumptions, historical basis, and failure conditions can be shown. Otherwise state a directional condition. Never turn an unsupported round-number drawdown into a purchase rule.

## Sector And Theme Handling

For stocks, combine business economics, revenue drivers, mainstream classifications, and market trading behavior. For funds, combine the contract category, benchmark, strategy, and latest disclosed look-through exposure.

Describe recent sector conditions only with decision-relevant evidence: relative performance, earnings or supply-demand change, policy or event catalysts, and capital or sentiment data when verifiable. Attribute source opinions as opinions; do not present a news narrative as proven causality.
