# DeepEquity — Agentic Company Research System

DeepEquity 是一个以 **Investment Master 为研究规范**的投资研究工具。输入标的代码和研究问题，它会查找、阅读公开资料，保存来源与研究笔记，生成中文 Markdown 投资研究报告。

我们希望把 Skill 中的研究方法，变成可以重复运行、保留资料和检查结果的程序。当前 MVP 支持 **美股公司完整研究**与**中国场外被动指数 / ETF 联接 / 增强指数 / 混合 / 债券基金研究**：同一个 Research Agent 负责分析和写作，程序负责取材、基金指标计算、保存材料、限制运行和基本检查。

**当前状态：公司与基金均已产出九章报告。**

## MVP 怎么工作

```text
用户问题 + 股票 ticker / 基金代码与份额 + 信息截止日期
                |
                v
runner.py -> Skill Loader -> Investment Master 股票 / 基金研究规则
                |
                v
一个 Research Agent（agent/research.py）：规划、取材、分析、按需计算
                |
                +<-> 股票：SEC 发现 / 搜索 / HTML 阅读 / 计算 / 保存笔记
                +<-> 基金：档案 / 净值与指标 / 披露发现 / HTML与文字PDF / 搜索 / 计算 / 笔记
                |
                v
已读来源 + Markdown 研究笔记
                |
                v
Report Synthesis（agent/synthesis.py）：直接生成 Markdown
                |
                v
Lightweight Check（runtime/reporting.py）：九章、非空、引用来自已读 URL
                |
                v
Markdown 投资研究报告
```

商业模式、行业、竞争与管理、财务预测、估值、风险和执行条件，由同一个 Research Agent 按 Skill 开展。规划在这个 Agent 内完成；报告合成是后续的模型调用。

基金分支使用基金九章模板，分别研究底层指数暴露、产品与份额质量、当前时点。净值序列与计算由程序完成，基金不走 SEC 或公司 DCF 研究步骤；基金、官方业绩基准、目标 ETF 的比较口径分别标注。

## 快速运行

需要 **Python 3.11+**（仓库使用 3.12）和 **uv**。在项目根目录安装依赖：

```powershell
uv sync --locked
```

首次配置时，将 [.env.example](.env.example) 复制为本地 `.env`，填写：

| 配置 | 用途 |
| --- | --- |
| `DEEP_EQUITY_MODEL` | 模型名称；当前支持 `anthropic:` 前缀和 Anthropic-compatible 接口 |
| `ANTHROPIC_API_KEY` | 对应模型服务的 API Key |
| `TAVILY_API_KEY` | 网页搜索 |
| `SEC_USER_AGENT` | SEC 访问标识，填写应用名称和自己的联系邮箱 |

**运行命令怎么看**

以下命令均在项目根目录执行：

```text
uv run --locked deepequity "研究问题" [参数]
```

- `uv run --locked`：按项目锁定的依赖版本运行。
- `deepequity`：本项目的命令行入口。
- `"研究问题"`：告诉 Agent 本次要研究什么，必须填写；建议保留英文双引号。
- 后面的参数：指定研究对象、日期、输出位置等，必填情况见下表。

**研究公司：以 NVIDIA 为例**

如果配置保存在项目根目录的 `.env` 中，最简命令为：

```powershell
uv run --locked deepequity "完整研究 NVIDIA 的基本面与投资情况" --ticker NVDA --env-file .env
```

也可以把当前采用的默认选项明确写出来，效果相同：

```powershell
uv run --locked deepequity "完整研究 NVIDIA 的基本面与投资情况" --ticker NVDA --target-type stock --market US --scope full --env-file .env
```

研究 Apple 时，将问题中的公司名改为 Apple，并将 `--ticker NVDA` 改为 `--ticker AAPL`。**当前必须显式填写股票代码，不会仅凭问题中的公司名自动选择代码。**

**研究基金：以 ETF 联接基金为例**

如果配置保存在 `.env` 中，最简命令为：

```powershell
uv run --locked deepequity "完整研究易方达沪深300ETF联接A，未知资料明确保留为缺口" --target-type fund --fund-code 110020 --fund-type etf_feeder --share-class A --env-file .env
```

也可以明确写出市场和研究范围，效果相同：

```powershell
uv run --locked deepequity "完整研究易方达沪深300ETF联接A，未知资料明确保留为缺口" --target-type fund --fund-code 110020 --fund-type etf_feeder --share-class A --market CN --scope full --env-file .env
```

基金代码必须保留前导零；份额类别必须与代码对应，例如不能用 A 类代码请求 C 类研究。

增强指数基金沿用同一命令和执行流程，将子类型改为 `enhanced_index`：

```powershell
uv run --locked deepequity "完整研究易方达沪深300指数精选增强A，核对增强效果、跟踪风险和重要缺口" --target-type fund --fund-code 010736 --fund-type enhanced_index --share-class A --env-file .env
```

如果模型服务默认开启 thinking，合成时可能耗尽输出额度却没有正文。010736 本次成功交付报告使用以下已有设置：在当前 PowerShell 会话中关闭合成阶段 thinking，并将单次最大输出设为 16384 token，再执行研究命令。研究阶段 thinking 保持原有行为，不修改 `.env` 文件：

```powershell
$env:DEEP_EQUITY_STRUCTURED_THINKING = "disabled"
$env:DEEP_EQUITY_MAX_OUTPUT_TOKENS = "16384"
```

混合基金同样复用这套流程和上述合成设置，子类型填写 `mixed`，例如研究财通成长优选混合 C 类：

```powershell
uv run --locked deepequity "完整研究财通成长优选混合C，核对实际资产配置、经理、份额历史和重要缺口" --target-type fund --fund-code 021528 --fund-type mixed --share-class C --env-file .env
```

债券基金也使用同一流程和上述合成设置，子类型填写 `bond`，例如研究中信保诚稳达债券 C 类：

```powershell
uv run --locked deepequity "完整研究中信保诚稳达债券C，核对利率、信用、杠杆、分红和重要缺口" --target-type fund --fund-code 006178 --fund-type bond --share-class C --env-file .env
```

债券分支先核对合同允许的投资范围，再分析实际持仓，区分纯债、含可转债或股票等风险。优先阅读债券组合、信用评级、回购融资、利率敏感性、经理和申赎规则；拿不到的久期、到期收益率和实时利差保留为缺口。债券基金不等于保本产品，历史年化收益也不是未来收益承诺。

**每个参数是什么意思，是否必填**

| 参数 | 公司研究 | 基金研究 | 含义与省略后的行为 |
| --- | --- | --- | --- |
| `"研究问题"` | **必填** | **必填** | 本次研究要求，是直接写在命令后的文字，不需要加参数名。 |
| `--ticker` | **必填** | 不适用 | 股票代码，例如 `NVDA`、`AAPL`。 |
| `--target-type` | 可省略 | **必填** | 研究对象类型：`stock` 或 `fund`。省略时按股票研究处理，不会自动识别基金。 |
| `--fund-code` | 不适用 | **必填** | 六位基金代码，例如 `110020`、`000051`。 |
| `--fund-type` | 不适用 | **必填** | `passive_index` 表示被动指数基金；`etf_feeder` 表示 ETF 联接基金；`enhanced_index` 表示增强指数基金；`mixed` 表示混合基金；`bond` 表示债券基金。当前不会自动选择类型。 |
| `--share-class` | 不适用 | **必填** | 份额类别，例如 `A`、`C`，必须与基金代码一致。 |
| `--market` | 可省略 | 可省略 | 公司目前仅支持 `US`，基金仅支持 `CN`；省略时按对应研究类型执行。 |
| `--scope` | 可省略 | 可省略 | 研究范围。目前仅支持 `full`，即完整研究；省略时也是完整研究，暂不支持 `focused`。 |
| `--as-of` | 可省略 | 可省略 | 信息截止日期，格式为 `YYYY-MM-DD`。默认本机运行当天，不允许未来日期。 |
| `--output-dir` | 可省略 | 可省略 | 输出根目录。默认 `data/runs`，每次会在其下创建独立运行目录。 |
| `--env-file` | 按配置方式决定 | 按配置方式决定 | 指定环境配置文件，例如 `.env`。省略时不会自动加载 `.env`，只使用进程中已有的环境变量。 |
| `--execution-path` | 可省略 | 可省略 | 执行路径。当前默认且仅支持 `mvp`，通常无需填写。 |
| `--help` | 可选 | 可选 | 显示命令帮助，不启动研究。可直接运行 `uv run --locked deepequity --help`。 |

表中“不适用”的参数不能混入对应命令，例如基金研究不能同时填写 `--ticker`。

**如果配置已经放在环境变量中，还能更简化吗？**

可以省略 `--env-file .env`。例如公司研究最少只需保留研究问题和股票代码：

```powershell
uv run --locked deepequity "完整研究 NVIDIA" --ticker NVDA
```

基金研究仍需明确对象类型、代码、基金类型和份额：

```powershell
uv run --locked deepequity "完整研究易方达沪深300ETF联接A" --target-type fund --fund-code 110020 --fund-type etf_feeder --share-class A
```

**完全不填研究问题和参数，无法启动研究。** 如果只是把凭据写在 `.env` 文件中，仍需添加 `--env-file .env`；文件存在本身不会触发自动加载。已有进程环境变量优先于该文件中的同名配置。

**当前支持范围和运行上限**

公司研究目前支持美股完整研究；基金研究目前支持中国场外被动指数基金、ETF 联接基金、增强指数基金、混合基金和债券基金。主动股票和 FOF 的研究路径尚未接入。程序会核对基金代码、类型、份额与净值身份，冲突时停止模型研究。

默认上限为 **300 秒、30 次模型调用、30 次 HTTP/搜索请求、50 次工具调用**，可通过 `.env.example` 中对应的 `DEEP_EQUITY_*` 配置调整。真实运行可能产生模型/API 费用，调用次数上限不等于金额预算。基金分支无需额外配置新的数据商凭据。

## 去哪里看结果

每次创建新的运行目录，默认位置为：

```text
data/runs/mvp/<run_id>/
+-- 2026-10-13-GOOG.md # 当前代码固定的最终报告名，基本检查通过后生成
+-- report.md          # 基金分支的最终报告；与上一行按类型二选一
+-- report.draft.md    # 流式保存的初稿
+-- report.repair.md   # 需要修复时生成，最多一次
+-- sources.jsonl      # 实际返回给模型的来源文本、URL 和日期
+-- notes.md           # 研究笔记和工具计算记录
+-- nav-<code>.json    # 仅基金：截止日前原始数值序列、来源及日期，供离线复算
+-- fund_data.json     # 仅基金：合成阶段使用的档案、指标、比较口径、费用情景及缺口
+-- inputs.json        # 本次输入及公开配置
+-- result.json        # 状态、错误、调用统计和检查结果
+-- events.jsonl       # 运行过程
+-- worker.log         # Worker 日志
```

最终报告第九部分汇总实际读取的来源，读取日期统一显示为 UTC+8；JSONL 保留原始时间戳。来源保存的是读取窗口和元数据，不另复制完整 HTML/PDF。运行受阻时，已保存的材料和草稿仍可查看。

| 状态 | 含义 |
| --- | --- |
| `generated` | 已生成最终报告，基本检查通过 |
| `partial` | 留下部分结果，正常交付尚未完成 |
| `failed` | 本次运行失败，查看错误和保留材料 |

**`generated` 不代表研究完整或结论正确。** `quality_accepted` 默认为空；还需检查 `result.json` 中的错误、报告内容及重要缺口。单次研究错误也可能在保留材料后继续生成报告。

## 已做到什么，还有哪些限制

当前已实现独立 MVP 执行链、股票五个研究工具、基金七个研究工具、来源和笔记持续保存、报告流式输出、一次 Markdown 修复，以及调用预算和进程总时限控制。

基金分支复用同一个执行链，增加基金档案、净值及风险计算、披露发现工具和文字 PDF 阅读。收益、最大回撤、恢复日期、波动率及对齐后的比较指标由程序计算，定义和数据日期随结果保存。未获得无风险利率时不计算 Sharpe；缺少官方基准日序列时，不把目标 ETF 对比冒称为官方基准跟踪误差。

增强指数分支按相同流程优先阅读策略、持仓、费用和官方基准表现。程序可从可识别的定期报告表格中提取所选份额的同期间收益，复算收益差并与保存的净值交叉核对；披露的跟踪误差和目标单独保留。信息比率及独立计算的官方基准跟踪误差，在缺少可靠基准日序列时保留为缺口。回撤恢复分别记录从峰值、谷底起算的日历天数。扣除运作费用后的净值收益不会再次扣除管理、托管费，也不会套用 ETF 联接基金的费用豁免。

混合基金分支区分合同配置范围、实际资产配置和官方基准权重，优先读经理、策略、持仓及上一独立报告期的组合。后增设的 C 类使用自己的历史，不能借用 A 类补足三年业绩。可识别的披露表按所选份额核对收益；综合产品概要中 A/C 费用分段处理，不把 C 类零申购费当成 A 类费率。第六章的标准绩效表由程序分别填写最新净值指标、同期间官方基准对照，避免模型错期并列；分析文字仍由模型生成。资料缺失时保留 gap，不强加指数跟踪目标或据单次持仓认定长期风格稳定。

债券基金复用混合基金的分期绩效表和前一期持仓读取，增加信用、利率和融资风险材料。分红日继续使用已公布的日收益率估算总收益，不把单位净值涨幅当作含分红收益。不同日期的费率披露如有冲突，会同时保留来源和缺口，并停止生成确定的 A/C 费用优劣情景。

006178 债券 C 类完成两次真实迭代，均生成九章报告；最新一次用时 187.078 秒，13 次模型、20 次数据请求、39 次工具调用，首次合成成功。1,959 条净值观测、14 次分红及七个官方披露期间通过复算，15 个引用 URL 均来自本次读取。两次之间补充了单券与发行人、限购等表述约束，因此不属于固定版本稳定性重复测试。[原始生成样例](examples/2026-10-19-006178-fund.md)仍有未经推导的买入阈值、利率上下行情景百分比混用及费用偏好等保留意见，**未通过全文无保留质量验收**；`quality_accepted` 保留为 null。详见[本地验收记录](data/runs/bond-fund-acceptance-2026-10-19.md)。目前真实债券报告样本仅限此基金，未证明所有债券品种与申赎安排均已验收。

021528 混合 C 类完成两次真实迭代，均生成九章报告。最后一次用时213.922秒，8次模型、18次数据请求、33次工具调用，首次合成成功。C类历史、四个官方披露期间和费用情景通过复算，[应用输出样例](examples/2026-10-17-021528-fund.md)未人工润色。两次使用相同查询、截止日和配置，期间修正了绩效表代码，因此不是固定版本稳定性重复测试。全文仍有未推导的观察阈值、未核实的替代品成本判断等保留意见，尚未通过全文无保留质量验收。

010736 增强指数共做了三次真实运行：两次生成报告、一次合成超时。最后一次在上述输出设置下用时 209.594 秒，8 次模型调用、17 次数据请求、29 次工具调用，首次合成生成九章报告。净值和六个官方披露期间收益经复算一致；[原始生成样例](examples/2026-10-17-010736-fund.md)仍有未经推导的观察阈值、对照 ETF 称谓和部分因果判断等保留意见，**未通过全文无保留质量验收**。最终补充的对照角色标注、基金全名识别通过离线验证，未再追加真实模型运行。

本地 Apple 与 NVIDIA 样本已生成完整九章报告。NVIDIA 运行还验证了研究阶段超时后，程序能够用已有材料继续交付；但人工复核发现 DCF 计算、预测一致性和新闻依据问题，**尚未达到连续三次报告质量验收通过的目标**。

基金分支已真实研究 110020，最近连续三次均生成报告，关键净值、收益、回撤、比较指标和 A/C 成本情景可复算。部分文字仍混用了目标 ETF 对比与官方基准的跟踪误差口径，或作出过强的费用、风险判断；**这三次是数据与交付检查通过，不是三次全文质量验收通过**。见[原始生成样例](examples/2026-10-15-110020-fund.md)和[验收记录](data/runs/fund-mvp-acceptance-2026-10-16.md)。普通被动指数基金已完成模拟端到端测试与 161725 的真实数据预检，尚未完成多个产品的真实报告验收。

当前限制：

- MVP 仅支持显式代码的 `stock + US + full` 或 `fund + CN + full`；基金仅接入上述五类，focused 问题暂不支持。
- 基金分支支持带文字层的 PDF；扫描件、加密文件、需要通用 JavaScript 渲染或受访问限制的来源仍可能无法读取。股票路径的 PDF 能力保持原状。
- 基金净值和档案当前接公开数据商接口，官方披露优先用于交叉核对；接口改版、分红信息缺失或数据冲突会留下 gap。当前档案快照不能证明历史时点的费率、规模或策略，历史研究需对应时期披露。
- URL 检查只能证明链接来自已读页面，不能证明每个结论得到正文支持，或网页内容在截止日期前已知。
- 基金收益使用单位净值比率，分红/折算事件日采用数据商披露的日增长率，并标注金额未独立重建的局限；风险按净值观察日频计算，年化波动采用 252 观察日约定。可用净值历史不一定对应始终不变的投资策略。
- 不自动验证股票 DCF、预测模型或所有研究判断。基金程序指标可复算，但报告能否正确解释指标、日期、费用和重要缺口，仍需人工复核。

报告目标风格可参考 [Apple Investment Master 示例](examples/2026-10-09-Apple-AAPL.md)。它是质量参考，不是当前 MVP 自动生成结果。生成报告用于研究辅助，不执行交易。

## 代码与离线验证

| 文件 | 职责 |
| --- | --- |
| [runner.py](research_agent/runner.py) | 解析 CLI，启动 MVP |
| [mvp.py](research_agent/mvp.py) | 加载 Skill、初始化模型和工具、协调两个阶段，统一管理预算、错误及运行记录 |
| [agent/research.py](research_agent/agent/research.py) | 按 Skill 规划、调用研究工具、分析，并通过回调持续保存笔记 |
| [agent/synthesis.py](research_agent/agent/synthesis.py) | 使用笔记、实际读取的来源和研究错误，流式生成报告，执行基本检查及最多一次修复 |
| [runtime/reporting.py](research_agent/runtime/reporting.py) | 整理 Markdown、检查章节和引用、生成来源列表 |
| [runtime/supervisor.py](research_agent/runtime/supervisor.py) | 监督 Worker 进程和总时限 |
| [tools/mvp.py](research_agent/tools/mvp.py) / [skill_loader.py](research_agent/skill_loader.py) | 提供五个研究工具 / 加载研究规则 |
| [tools/fund.py](research_agent/tools/fund.py) | 提供基金数据工具、披露和 PDF 阅读，复用共同预算与保存机制 |
| [fund_types.py](research_agent/fund_types.py) | 集中选择基金子类型的取材位置与执行重点，五类共用研究和报告流程 |
| [tools/fund_data.py](research_agent/tools/fund_data.py) / [fund_metrics.py](research_agent/tools/fund_metrics.py) | 安全解析数据 / 按明确期间计算指标，不执行数据商 JavaScript |
| [tools/pdf.py](research_agent/tools/pdf.py) / [agent/instructions.py](research_agent/agent/instructions.py) | 在内存读取 PDF 文字窗口 / 分离股票与基金执行提示词 |

研究与合成共享同一次运行的模型和预算。两个阶段通过明确参数接收材料，通过回调请求模型调用额度、记录响应及保存诊断；运行协调器负责处理异常和关闭客户端。股票沿用现有固定报告名，基金使用 `report.md`。

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q tests/test_fund_data.py tests/test_fund_mvp.py tests/test_agent_stages.py tests/test_mvp.py tests/test_mvp_tools.py tests/test_mvp_deadline.py
```

这些测试验证执行、保存、引用边界和时限，不代表真实投资报告准确率。旧结构化工作流、Studio/Notebook 入口和回放脚本已从活动代码中移除。

项目源自 DeepAgents Deep Research scaffold，来源说明见 [UPSTREAM.md](UPSTREAM.md)；内置 Investment Master 快照与许可位于 [research_agent/skills/investment-master/](research_agent/skills/investment-master/)。
