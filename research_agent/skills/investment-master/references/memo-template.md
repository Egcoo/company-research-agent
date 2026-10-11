# Stock Research Memo Template

Create the report in `reports/` using `YYYY-MM-DD-target-name-code.md`. Use exactly the nine primary sections below unless the user requests another structure. Optimize for decision clarity, not length.

**目录**

1. 投资结论
2. 公司到底是怎么赚钱的
3. 所属行业的投资逻辑
4. 公司竞争力与管理质量
5. 财务质量与盈利预测
6. 估值与预期收益
7. 催化剂、主要风险与逻辑失效条件
8. 最终购买建议与执行方案
9. 免责声明和数据来源

```markdown
# {股票名称}（{代码}）投资研究报告

> 报告日期：{YYYY-MM-DD}  
> 信息截止：{single cutoff; older reporting periods must be labeled separately}  
> 计价货币与证券形式：{currency; ordinary share/ADR/ADS/share class and conversion ratio when relevant}  
> 判断边界：{user premise, or “仅评价公司、股票与当前时点；因缺少个人风险承受能力、持有期、仓位及组合信息，不判断个人适配性”}  
> 说明：本文为基于公开信息的投资研究支持，不构成个人化投资建议。

## 1. 投资结论

| 项目 | 结论 | 口径/依据 |
|---|---|---|
| 股票/代码 | {name/code} | {exchange/security form} |
| 当前股价 | {price and currency} | {close/intraday, date, source} |
| 当前市值 | {market cap and currency} | {date, provider or price × share-count basis, source} |
| 评级 | {买入/谨慎买入/持有/观察/回避} | {new-purchase or existing-holder judgment} |
| 基准合理价值 | {value or explicit evidence gap} | {method and valuation date} |
| 合理价值区间 | {range or explicit evidence gap} | {key assumptions and uncertainty} |
| 相对当前价格空间 | {upside/downside} | `{fair value ÷ current price - 1}` |
| 主要技术支撑 | {level/range or explicit evidence gap} | {series cutoff, adjustment, interval, repeatable method} |
| 建议观察/持有期限 | {6-12 months/2-3 years/long term} | {match the forecast and catalyst horizon} |
| 组合定位 | {core/growth satellite/cyclical/defensive/special situation} | {exposure and main source of risk} |
| 证据状态 | {充分/部分缺口/不足} | {decisive unresolved item} |

**一句话逻辑：** {what the market may be mispricing + what must be delivered + the principal trade-off}

### 三条核心投资理由

| 理由 | 已核实证据及日期 | 对盈利或估值的含义 |
|---|---|---|
| 1. {reason} | {fact + close citation} | {implication} |
| 2. {reason} | {fact + close citation} | {implication} |
| 3. {reason} | {fact + close citation} | {implication} |

### 三条主要风险摘要

| 风险 | 传导机制 | 关键预警信号 |
|---|---|---|
| 1. {risk} | {mechanism and consequence} | {observable signal} |
| 2. {risk} | {mechanism and consequence} | {observable signal} |
| 3. {risk} | {mechanism and consequence} | {observable signal} |

{Section 1 states the decision only. Do not repeat the full evidence developed below.}

## 2. 公司到底是怎么赚钱的

### 标的信息

| 项目 | 内容 | 数据日期/报告期 | 来源 |
|---|---|---|---|
| 公司/代码/上市地 | {content} | {date} | [source](URL) |
| 证券形式/计价货币 | {content} | {date} | [source](URL) |
| 主营业务 | {content} | {period} | [source](URL) |
| 关键规模 | {revenue/assets/users/capacity as appropriate} | {period} | [source](URL) |
| 股本口径 | {basic/diluted/ordinary shares/ADS ratio} | {date} | [source](URL) |

### 产品、客户与收费方式

{Explain the material products or services, customer, value delivered, pricing model, and revenue recognition. Do not copy the company profile.}

### 分部收入与利润来源

| 业务 | 收入/占比 | 利润贡献或利润率 | 增长速度 | 核心驱动 | 证据状态 |
|---|---:|---:|---:|---|---|
| 核心业务 | {content} | {content} | {content} | {volume/price/mix/etc.} | {verified/gap} |
| 第二增长曲线 | {content} | {content} | {content} | {content} | {verified/gap} |
| 其他业务 | {content} | {content} | {content} | {content} | {verified/gap} |

### 成本、现金流与周期性

{Explain major costs, fixed/variable structure, working capital, capital intensity, cash conversion, and cyclicality. Separate real profit engines from narratives not yet producing profit.}

**商业模式一句话：** {the company creates what value for whom, charges how, and converts it into revenue and cash flow}

## 3. 所属行业的投资逻辑

### 所属板块与主题

- **官方分类：** {source-backed classification}
- **实际经济暴露：** {revenue, customer, product, geography, or factor exposure}
- **判断依据：** {why the classification matters economically}
- **相关行业指数与可比公司：** {appropriate benchmark and peers}

### 长期逻辑：未来 3—5 年

{Analyze demand, penetration, technology, regulation, capacity, channels, competition, and the industry's structural opportunity and risk. Do not discuss the company's moat here.}

### 所属板块近期状态：未来 6—18 个月

| 变量 | 当前状态与日期 | 变化方向/证据 | 对行业和公司的可能影响 |
|---|---|---|---|
| 需求/订单 | {baseline or gap} | {verified direction} | {research inference} |
| 价格/库存/产能利用率 | {baseline or gap} | {verified direction} | {research inference} |
| 政策/技术/事件 | {fact or sourced opinion} | {content} | {research inference} |
| 相对表现/资金/情绪 | {same-period evidence or gap} | {content} | {research inference} |

**行业判断：** {structural growth / short cycle / both / contraction}；{state the decisive evidence and what would change this view}。

## 4. 公司竞争力与管理质量

### 竞争优势及其验证结果

| 潜在优势 | 可验证结果 | 同行或历史对照 | 持续性判断 |
|---|---|---|---|
| {brand/cost/technology/network/switching/channel/license/scale/data/supply chain} | {market share/margin/retention/pricing/R&D conversion/ROIC} | {comparison and source} | {strong/weak/uncertain + why} |

{Explain why economic profit should accrue to this company rather than competitors. Do not treat management claims as proof.}

### 管理层、治理与资本配置

| 维度 | 已核实事实 | 评价与警示信号 |
|---|---|---|
| 履历、诚信与执行 | {content} | {inference} |
| 控股股东、激励与关联交易 | {content} | {inference} |
| 分红、回购与融资稀释 | {content} | {inference} |
| 并购、债务与再投资回报 | {content} | {inference} |

**竞争力与管理结论：** {moat strength/trend + capital-allocation quality + unresolved gap}

## 5. 财务质量与盈利预测

### 历史财务质量

| 指标 | {FY-2A} | {FY-1A} | {FYA/LTM} | 口径与研究含义 |
|---|---:|---:|---:|---|
| 营业收入/增速 | {value} | {value} | {value} | {currency, period, implication} |
| 毛利率 | {value} | {value} | {value} | {content} |
| 营业利润率 | {value} | {value} | {value} | {content} |
| 归母净利润 | {value} | {value} | {value} | {GAAP/non-GAAP and adjustments} |
| 经营现金流 | {value} | {value} | {value} | {content} |
| 自由现金流 | {value} | {value} | {value} | {formula} |
| ROE/ROIC | {value} | {value} | {value} | {formula and capital base} |
| 资产负债率/有息负债 | {value} | {value} | {value} | {content} |
| 应收账款/存货 | {value} | {value} | {value} | {growth versus revenue} |
| 股本稀释 | {value} | {value} | {value} | {basic/diluted basis} |

### 盈利质量与报表附注

{Investigate profit-cash mismatch, receivables or inventory growing faster than revenue, non-recurring items, revenue recognition, capitalized costs, provisions, leases/off-balance-sheet obligations, and accounting changes.}

### 盈利预测驱动与假设

**驱动公式：** `{segment revenue = volume × price}`；`{profit = revenue × margin - expenses}`；{company-specific bridge}。

| 业务/指标 | {FYA} | {FY+1E} | {FY+2E} | 关键假设与敏感变量 |
|---|---:|---:|---:|---|
| 业务 A 收入 | {value} | {value} | {value} | {volume/price/mix} |
| 业务 B 收入 | {value} | {value} | {value} | {customer/penetration/capacity} |
| 毛利率/营业利润率 | {value} | {value} | {value} | {mix/competition/operating leverage} |
| 归母净利润/每股收益 | {value} | {value} | {value} | {tax/share count/adjustments} |
| 自由现金流 | {value} | {value} | {value} | {capex/working capital} |

**预测结论：** {what must happen for the forecast, the largest sensitivity, and what evidence would falsify it}

## 6. 估值与预期收益

### 估值方法与口径

- **主方法：** {DCF/DDM/FCF yield/PE/PEG/EV-EBITDA/PB/residual income/embedded value/mid-cycle/SOTP}；{why it fits}
- **交叉验证：** {appropriate absolute or relative method; or evidence gap}
- **统一口径：** {currency, valuation date, share count, net debt, minority interest, lease and per-share conversion}

### 当前价格隐含了什么

{Reverse the current price into implied revenue growth, margin, success probability, return on capital, or growth duration. Show the formula/inputs and judge whether expectations are demanding or conservative.}

### 三种情景估值

| 情景 | 盈利/现金流假设 | 估值假设 | 合理价值 | 相对当前价格 | 失效变量 |
|---|---|---|---:|---:|---|
| 乐观 | {content} | {content} | {value} | {percent} | {content} |
| 基准 | {content} | {content} | {value} | {percent} | {content} |
| 悲观 | {content} | {content} | {value} | {percent} | {content} |

- **基准合理价值：** {value or gap}
- **合理价值区间：** {range or gap}
- **悲观情景下行风险：** {value and mechanism}
- **估值不确定性：** {low/medium/high + source of uncertainty}
- **安全边际判断：** {whether current price compensates for uncertainty}

{Do not provide a target or range that cannot be reproduced from the stated assumptions.}

## 7. 催化剂、主要风险与逻辑失效条件

### 催化剂

| 催化剂 | 可能时间窗 | 验证证据 | 对盈利或估值的作用 |
|---|---|---|---|
| {event} | {window} | {observable evidence} | {repricing mechanism} |

### 主要风险

| 风险 | 传导机制与后果 | 当前基线 | 预警信号/来源 |
|---|---|---|---|
| {demand/competition/technology/customer/margin/leverage/cash/governance/regulation/FX/dilution/valuation} | {mechanism} | {value or gap} | {trigger + source} |

### 逻辑失效条件

| 失效条件 | 可观察证据 | 触发后的行动 |
|---|---|---|
| {condition} | {metric/event and period} | {downgrade/revalue/exit/reassess} |

### 空头视角：看多逻辑最可能错在哪里

{State the strongest evidence-backed counter-thesis, not a generic risk disclaimer. Explain what observable development would prove it right.}

## 8. 最终购买建议与执行方案

### 最终评级与购买方式

- **最终评级：** {买入/谨慎买入/持有/观察/回避}
- **购买方式：** {one-time/staged/wait for results/wait for valuation/do not participate}
- **开始或追加条件：** {valuation + fundamental/industry/technical confirmation with reproducible basis}
- **为什么不是更积极/更消极的评级：** {main trade-off}

### 组合定位、期限与仓位原则

- **组合定位：** {core/growth satellite/cyclical/defensive/special situation}
- **适用观察/持有期限：** {horizon matched to thesis}
- **仓位原则：** {uncertainty, volatility, concentration, correlation, and pessimistic loss; no arbitrary percentage without user context}
- **个人适配性：** {state what can and cannot be concluded from supplied user information}

### 卖出或重新评估条件

1. {price reaches fair value or expected return becomes unattractive}
2. {fundamental thesis is falsified}
3. {management/governance/capital allocation deteriorates}
4. {portfolio or user constraint changes, only when known}

### 后续观察指标

| 指标 | 当前基线/缺口 | 触发方向或条件 | 来源/更新频率 | 对判断的影响 |
|---|---|---|---|---|
| {indicator} | {value/date or gap} | {direction/threshold} | {source/frequency} | {upgrade/downgrade/revalue} |

## 9. 免责声明和数据来源

### 信息口径与方法

- **信息截止：** {date and market close/intraday convention}
- **财务口径：** {reporting standard, currency, fiscal periods, A/E labels, GAAP/non-GAAP treatment}
- **估值口径：** {methods, share count, FX, net debt and per-share conversion}
- **技术分析口径：** {series, adjustment, interval and method; or explicit gap}
- **证据状态：** {充分/部分缺口/不足}

### 关键数据来源

| 类型 | 主要来源 | 覆盖内容 | 截止/报告期 |
|---|---|---|---|
| 公司与交易所原始资料 | [source](URL) | {filings, announcements, share data} | {date/period} |
| 行业与监管资料 | [source](URL) | {industry, policy, statistics} | {date/period} |
| 市场与估值数据 | [source](URL) | {price, valuation, peers} | {date/period} |

### 未核实或证据不足的信息

- {gap + why it matters + source needed to resolve it}

### 免责声明

本文基于公开资料进行研究整理，可能存在信息滞后、口径差异、预测偏差和未覆盖风险；文中结论仅为研究判断，不构成面向任何个人的投资建议、收益承诺或交易指令。投资者应结合自身风险承受能力、持有期限、流动性需求和组合情况独立决策。
```

### Drafting Rules

- Keep exactly the nine primary sections and keep their names and order. Use subsections to adapt depth; do not add a tenth primary section.
- Treat section 1 as a decision page. Put canonical evidence and full reasoning in sections 2-8 and cite decisive facts close to the claims.
- Keep `标的信息`, `所属板块与主题`, `所属板块近期状态`, `主要风险`, and `后续观察指标` as the named subsections shown above. If evidence is unavailable, retain the subsection and state the gap.
- Keep actuals, estimates, source opinions, research inferences, and final judgments distinct.
- Use the same price, share count, currency, reporting period, and valuation definitions throughout. Explain any intentional alternative definition.
- Require a reproducible bridge from forecast assumptions to valuation and from fair value to upside/downside.
- Require technical support levels to identify the price series, cutoff, adjustment, interval, method, and failure condition. State an evidence gap instead of inventing a level.
- Make the final rating in sections 1 and 8 identical. `持有` applies only to existing holders.
- Remove repeated descriptions and non-decisive facts. Every table or paragraph must add information that can affect the judgment.
