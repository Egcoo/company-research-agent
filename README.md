# DeepEquity — Agentic Company Research System

DeepEquity 是一个以 **Investment Master 为研究规范**的上市公司研究工具。输入公司代码和研究问题，它会查找、阅读公开资料，保存来源与研究笔记，生成中文 Markdown 投资研究报告。

我们希望把 Skill 中“应该怎样研究一家公司”的方法，变成可以重复运行、保留资料和检查结果的程序。当前先做好 **美股公司的完整研究 MVP**：模型负责分析和写作，程序负责取材工具、保存材料、限制运行和基本检查。

**当前状态：Apple 和 NVIDIA 的真实运行已产出九章报告**

## MVP 怎么工作

```text
用户问题 + ticker + 信息截止日期
                |
                v
runner.py -> Skill Loader -> Investment Master 股票研究规则
                |
                v
一个 Research Agent：规划、取材、分析、按需计算
                |
                +<-> Tools：SEC 发现 / 搜索 / 阅读 / 计算 / 保存笔记
                |
                v
已读来源 + Markdown 研究笔记
                |
                v
Report Synthesis：直接生成 Markdown
                |
                v
Lightweight Check：九章、非空、引用来自已读 URL
                |
                v
report.md
```

商业模式、行业、竞争与管理、财务预测、估值、风险和执行条件，由同一个 Research Agent 按 Skill 开展。规划在这个 Agent 内完成；报告合成是后续的模型调用。

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

运行一次 NVIDIA 完整研究：

```powershell
uv run --locked deepequity "完整研究 NVIDIA 的基本面与投资情况" --execution-path mvp --ticker NVDA --target-type stock --market US --scope full --env-file .env
```

- 将 `NVDA` 和问题中的公司名改为 `AAPL` / Apple，即可研究 Apple。
- `--as-of YYYY-MM-DD` 指定信息截止日期，默认当天；`--output-dir` 指定输出根目录。
- **必须显式传入 `--execution-path mvp`**：CLI 当前默认值仍为旧的 `skill` 路径。
- 只有显式指定 `--env-file` 才会读取本地环境文件，已有进程环境变量优先。真实运行会调用外部服务，可能产生模型/API 费用。

默认上限为 300 秒、30 次模型调用、30 次 HTTP/搜索请求和 50 次工具调用，可通过 `.env.example` 中对应的 `DEEP_EQUITY_*` 配置调整。调用次数上限不是金额预算。

## 去哪里看结果

每次创建新的运行目录，默认位置为：

```text
data/runs/mvp/<run_id>/
+-- report.md          # 基本检查通过后的最终报告
+-- report.draft.md    # 流式保存的初稿
+-- report.repair.md   # 需要修复时生成，最多一次
+-- sources.jsonl      # 实际返回给模型的来源文本、URL 和日期
+-- notes.md           # 研究笔记和工具计算记录
+-- inputs.json        # 本次输入及公开配置
+-- result.json        # 状态、错误、调用统计和检查结果
+-- events.jsonl       # 运行过程
+-- worker.log         # Worker 日志
```

最终报告第九部分汇总实际读取的来源。来源保存的是读取窗口和元数据，不另复制完整 HTML/PDF。运行受阻时，已保存的材料和草稿仍可查看。

| 状态 | 含义 |
| --- | --- |
| `generated` | 已生成最终报告，基本检查通过 |
| `partial` | 留下部分结果，正常交付尚未完成 |
| `failed` | 本次运行失败，查看错误和保留材料 |

**`generated` 不代表研究完整或结论正确。** `quality_accepted` 默认为空；还需检查 `result.json` 中的错误、报告内容及重要缺口。单次研究错误也可能在保留材料后继续生成报告。

## 已做到什么，还有哪些限制

当前已实现独立 MVP 执行链、五个研究工具、来源和笔记持续保存、报告流式输出、一次 Markdown 修复，以及调用预算和进程总时限控制。

本地 Apple 与 NVIDIA 样本已生成完整九章报告。NVIDIA 运行还验证了研究阶段超时后，程序能够用已有材料继续交付；但人工复核发现 DCF 计算、预测一致性和新闻依据问题，**尚未达到连续三次报告质量验收通过的目标**。

当前限制：

- MVP 仅支持显式 ticker 的美股 `stock + full`；基金和 focused 问题属于保留的旧路径。
- PDF/OCR、需要 JavaScript 的网页和受访问限制的来源可能无法读取。行情依赖可读网页，尚无独立行情接口或完整历史数据库。
- URL 检查只能证明链接来自已读页面，不能证明每个结论得到正文支持，或网页内容在截止日期前已知。
- 数字单位和期间只做粗略提示；不自动验证 DCF、预测模型或所有事实。重要缺口应在报告中保留，仍需人工复核。

报告目标风格可参考 [Apple Investment Master 示例](examples/2026-10-09-Apple-AAPL.md)。它是质量参考，不是当前 MVP 自动生成结果。生成报告用于研究辅助，不执行交易。

## 代码与离线验证

主要入口：[runner.py](research_agent/runner.py)；执行与报告：[mvp.py](research_agent/mvp.py)；研究工具：[tools/mvp.py](research_agent/tools/mvp.py)；规则加载：[skill_loader.py](research_agent/skill_loader.py)。

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q tests/test_mvp.py tests/test_mvp_tools.py tests/test_mvp_deadline.py
```

这些测试验证执行、保存、引用边界和时限，不代表真实投资报告准确率。旧结构化流程及 `examples/` 中的两个回放/对照脚本仍保留，MVP 不调用它们。

项目源自 DeepAgents Deep Research scaffold，来源说明见 [UPSTREAM.md](UPSTREAM.md)；内置 Investment Master 快照与许可位于 [research_agent/skills/investment-master/](research_agent/skills/investment-master/)。
