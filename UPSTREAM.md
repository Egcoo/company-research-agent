# Upstream attribution and scaffold baseline

## Project identity

| Name | Meaning |
| --- | --- |
| `company-research-agent` | Repository and local project directory |
| DeepEquity — Agentic Company Research System | Product and portfolio name |
| `research_agent` | Python import package |
| `langchain-ai/deepagents/examples/deep_research` | Upstream scaffold |

This is an independent development repository built from the DeepAgents Deep Research example. It does not vendor the entire DeepAgents repository or modify the DeepAgents framework.

## Source and license

- Upstream repository: [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents).
- Scaffold path: `examples/deep_research/`.
- Verified reference snapshot: [`081ebb2246284c58f85ead56c8be535020cfd123`](https://github.com/langchain-ai/deepagents/tree/081ebb2246284c58f85ead56c8be535020cfd123/examples/deep_research).
- Verification date: 2026-10-04, against the existing local upstream checkout with a clean example directory.
- Upstream copyright: LangChain, Inc. The complete upstream MIT notice is preserved in [licenses/deepagents-MIT.txt](licenses/deepagents-MIT.txt).
- The existing project [LICENSE](LICENSE) is retained separately.

The original extraction commit was not recorded. The reference snapshot above identifies matching content; it is not a claim about when or from which commit the files were originally copied.

## Unmodified upstream files

At baseline creation, SHA-256 comparisons matched all nine files against the reference checkout:

```text
agent.py
research_agent/__init__.py
research_agent/prompts.py
research_agent/tools.py
pyproject.toml
README.md
utils.py
langgraph.json
research_agent.ipynb
```

These files retain their existing content, including the upstream README, prompts, dependency declarations, and example project metadata. Baseline creation does not upgrade or refactor them.

## Retained local configuration

The current `uv.lock`, `.python-version`, and `.env.example` are recorded as local baseline configuration. They are not asserted to be identical to the reference scaffold. The Python version remains `3.12`; the environment template contains placeholders and is tracked, while the local `.env` is excluded.

Both lockfiles contain 197 packages. The retained local lockfile differs from the reference in these package versions:

| Package | Local baseline | Upstream reference |
| --- | --- | --- |
| `jupyterlab` | 4.5.10 | 4.5.11 |
| `notebook` | 7.5.6 | 7.5.7 |
| `tornado` | 6.5.8 | 6.5.9 |

No dependency files are changed to reconcile these differences. The baseline also includes the repository ignore rules, this attribution record, and the upstream MIT notice.

## Development boundary

The local commit titled `chore: establish upstream deep research scaffold baseline` records this scaffold and its necessary configuration before DeepEquity business development begins. It excludes the existing `docs/` work, local credentials, virtual environments, IDE files, and generated research output.

Future DeepEquity changes should be reviewed against that commit's SHA. The baseline does not claim to implement SEC research, Evidence management, RAG, or a DeepEquity report workflow. M1.1 and later work belong to subsequent changes; the upstream README remains unchanged in this baseline.
