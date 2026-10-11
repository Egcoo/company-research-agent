"""Replay saved legacy research through the new report path without network calls."""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from research_agent.config import ResearchBudget, ResearchError, Settings
from research_agent.evidence import EvidenceLedger
from research_agent.report import export_run, render_report
from research_agent.schemas import Judgment, Target, TaskDraft, TaskResult
from research_agent.validation import bind_tasks, validate_judgment, validate_ledger
from research_agent.workflow import ReportDraft, ResearchRun

_REPLAY_LIMIT = "纯离线历史回放：未调用模型或重新获取来源，未重新核验身份、原文对历史陈述的支持或投资判断。"
_FINDING_LIMIT = (
    "这是历史已保存的研究陈述，未执行新的研究推断或语义核验；引用仅追溯到保存材料。"
)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ResearchError(
            "replay_invalid_artifact",
            "A saved replay artifact could not be read as UTF-8 JSON.",
        ) from None


def _registry(records, field: str) -> dict:
    if not isinstance(records, list):
        raise ResearchError(
            "replay_invalid_artifact", "Saved research registries must be lists."
        )
    registry = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get(field), str)
            or not record[field].strip()
            or record[field] in registry
        ):
            raise ResearchError(
                "replay_invalid_artifact",
                "Saved research registry identifiers must be unique and nonempty.",
            )
        registry[record[field]] = record
    return registry


def _strings(values) -> list[str]:
    return [value for value in values or [] if isinstance(value, str) and value.strip()]


def _saved_report(run_dir: Path, result: dict) -> tuple[dict | None, str | None]:
    for filename in ("report_draft.json", "report_original.json", "report.json"):
        content = _read_json(run_dir / filename, None)
        if isinstance(content, dict):
            report = content.get("report", content)
            if isinstance(report, dict):
                return report, filename
    report = result.get("report")
    return (report, "result.json") if isinstance(report, dict) else (None, None)


def _check_company(company: dict | None, expected: dict) -> None:
    if not company:
        return
    if not isinstance(company, dict):
        raise ResearchError(
            "replay_invalid_artifact", "Saved company identities must be objects."
        )
    for field in ("ticker", "cik"):
        actual, target = company.get(field), expected.get(field)
        if actual and target and str(actual).upper() != str(target).upper():
            raise ResearchError(
                "target_mismatch",
                "Saved source identities do not match the replay target.",
            )


def _known_references(report: dict | None, evidence: dict) -> None:
    if not report:
        return
    references = []
    for section in report.get("sections", []):
        if isinstance(section, dict):
            for claim in section.get("claims", []):
                references.extend(claim.get("evidence_ids", []))
    for note in report.get("notes", []):
        references.extend(note.get("evidence_ids", []))
    for excerpt in report.get("excerpts", []):
        references.append(excerpt.get("evidence_id"))
    if any(
        not isinstance(identifier, str) or identifier not in evidence
        for identifier in references
    ):
        raise ResearchError(
            "unknown_evidence",
            "A saved report references an unregistered legacy evidence identifier.",
        )


def replay_skill_report(run_dir, output_dir=None) -> dict:
    """Preserve saved statements as unresolved material and render a partial replay.

    With no output_dir, return the replay entirely in memory. An explicit output
    directory receives a fresh UUID run; the source directory cannot be a target.
    """
    source_dir = Path(run_dir).resolve()
    output = Path(output_dir).resolve() if output_dir is not None else None
    if output is not None and (output == source_dir or source_dir in output.parents):
        raise ResearchError(
            "unsafe_replay_output",
            "Replay output must be outside the original run directory.",
        )
    plan = _read_json(source_dir / "plan.json", {})
    old_result = _read_json(source_dir / "result.json", {})
    if not isinstance(plan, dict) or not isinstance(old_result, dict):
        raise ResearchError(
            "replay_invalid_artifact", "A legacy plan and result must be JSON objects."
        )
    sources = _registry(_read_json(source_dir / "sources.json", []), "source_id")
    old_evidence = _registry(
        _read_json(source_dir / "evidence.json", []), "evidence_id"
    )
    report, report_file = _saved_report(source_dir, old_result)
    company = plan.get("company") or (report or {}).get("company")
    if not company:
        company = next(
            (
                record.get("company")
                for record in sources.values()
                if record.get("company")
            ),
            None,
        )
    cutoff = plan.get("as_of") or (report or {}).get("as_of")
    if not isinstance(company, dict) or not company.get("name") or not cutoff:
        raise ResearchError(
            "replay_context_missing",
            "Replay requires a saved target identity and research cutoff; they will not be guessed.",
        )
    try:
        as_of = date.fromisoformat(cutoff)
        target = Target(
            kind="stock",
            name=company["name"],
            symbol=company.get("ticker"),
            identity_status="unresolved",
            identity_source="legacy_saved_artifacts",
            adapter_metadata={"cik": company["cik"]} if company.get("cik") else {},
        )
    except (ValueError, TypeError, ValidationError):
        raise ResearchError(
            "replay_context_missing", "Saved target identity or cutoff is invalid."
        ) from None
    _known_references(report, old_evidence)
    settings = Settings()
    budget = ResearchBudget(settings)
    offline_resource = SimpleNamespace(budget=budget, close=lambda: None)
    run = ResearchRun(
        settings,
        query=plan.get("query", "历史保存材料离线回放"),
        as_of=as_of,
        target=target,
        scope="focused",
        fetcher=offline_resource,
        search=offline_resource,
    )
    run.ledger = EvidenceLedger(as_of, target.key)
    run.tasks = bind_tasks(
        [
            TaskDraft(
                task_type="historical_replay",
                objective="回放历史保存材料，不执行新的研究或模型调用。",
            )
        ]
    )
    task_id = run.tasks[0].task_id
    gaps = [
        _REPLAY_LIMIT,
        "旧来源按保存的 snippets 顺序连接；读取窗口可能重复或不连续，不能视为完整原始页面。",
    ]
    source_mapping, evidence_mapping, spans = {}, {}, {}
    for identifier, record in sources.items():
        _check_company(record.get("company"), company)
        snippets = record.get("snippets", [])
        if not isinstance(snippets, list) or any(
            not isinstance(snippet, dict) or not isinstance(snippet.get("content"), str)
            for snippet in snippets
        ):
            raise ResearchError(
                "replay_invalid_artifact", "Saved source snippets must contain text."
            )
        text = "\n\n".join(snippet["content"] for snippet in snippets)
        if not text.strip():
            gaps.append(f"历史来源 {identifier} 没有保存可回放正文。")
            continue
        try:
            published_at = (
                date.fromisoformat(record["published_at"])
                if record.get("published_at")
                else None
            )
        except (TypeError, ValueError):
            raise ResearchError(
                "replay_invalid_artifact", "A saved source publication date is invalid."
            ) from None
        if not isinstance(record.get("source_url"), str):
            raise ResearchError(
                "replay_invalid_artifact", "A saved source requires its original URL."
            )
        source = run.ledger.add_source(
            record["source_url"],
            record.get("source_title") or "历史来源",
            text,
            record.get("source_type") or "legacy",
            published_at,
        )
        if record.get("fetched_at"):
            try:
                fetched_at = datetime.fromisoformat(record["fetched_at"])
            except (TypeError, ValueError):
                raise ResearchError(
                    "replay_invalid_artifact",
                    "A saved source retrieval timestamp is invalid.",
                ) from None
            source.retrieved_at = fetched_at
            run.ledger.sources[source.source_id].retrieved_at = fetched_at
        else:
            gaps.append(
                f"历史来源 {identifier} 未保存获取时间；新登记时间只是回放时间，不代表重新获取。"
            )
        source_mapping[identifier] = source
    for identifier, record in old_evidence.items():
        _check_company(record.get("company"), company)
        source_id = record.get("source_id")
        if source_id not in sources:
            raise ResearchError(
                "unknown_source",
                "A saved evidence item references an unregistered legacy source.",
            )
        source = source_mapping.get(source_id)
        content = record.get("content")
        if source is None or not isinstance(content, str) or not content:
            gaps.append(
                f"历史证据 {identifier} 的原文或来源正文不可用，未猜测对应位置。"
            )
            continue
        start = source.text.find(content)
        if start < 0 or source.text.find(content, start + 1) >= 0:
            gaps.append(
                f"历史证据 {identifier} 的保存原文无法唯一定位，未猜测对应位置。"
            )
            continue
        statement = record.get("claim")
        if not isinstance(statement, str) or not statement.strip():
            gaps.append(f"历史证据 {identifier} 没有保存研究陈述，未生成新陈述。")
            continue
        evidence = run.ledger.record(
            task_id,
            source.source_id,
            start // 6000,
            "历史摘取陈述",
            statement,
            "unresolved",
            "来自旧 evidence.claim 的保存陈述，未重新核验。",
        )
        evidence_mapping[identifier] = evidence.evidence_id
        spans[identifier] = {
            "source_id": source.source_id,
            "start": start,
            "end": start + len(content),
        }
    texts = []
    if report:
        _check_company(report.get("company"), company)
        gaps.extend(_strings(report.get("gaps")))
        gaps.extend(_strings(report.get("limitations")))
        for section in report.get("sections", []):
            if isinstance(section, str):
                texts.append(section)
                continue
            heading = section.get("heading") or "历史保存答复"
            claims = section.get("claims", [])
            saved_text = []
            for claim in claims:
                statement = claim.get("text")
                if not isinstance(statement, str) or not statement.strip():
                    continue
                saved_text.append(statement)
                references = [
                    evidence_mapping[identifier]
                    for identifier in claim.get("evidence_ids", [])
                    if identifier in evidence_mapping
                ]
                limits = [_FINDING_LIMIT]
                if len(references) < len(claim.get("evidence_ids", [])):
                    limits.append(
                        "部分历史证据无法迁移定位；本条只保留原保存陈述，不声称已支持或核实。"
                    )
                run.ledger.add_finding(task_id, heading, references, statement, limits)
            texts.append(f"### {heading}\n\n" + "\n\n".join(saved_text))
        notes = _strings([note.get("text") for note in report.get("notes", [])])
        if notes:
            texts.append(
                "### 历史保存备注（不使用原文关联键）\n\n" + "\n\n".join(notes)
            )
        for note in report.get("notes", []):
            if note.get("kind") in {"gap", "limitation", "unclassified"}:
                gaps.extend(_strings([note.get("text")]))
    else:
        gaps.append("未保存可回放的历史报告或投资判断；本次只展示来源和旧证据陈述。")
        texts.append(
            "没有保存的历史报告或投资判断。以下仅回放已有来源和旧证据摘取陈述。"
        )
        for identifier, mapped in evidence_mapping.items():
            statement = old_evidence[identifier]["claim"]
            texts.append(statement)
            run.ledger.add_finding(
                task_id, "历史摘取陈述", [mapped], statement, [_FINDING_LIMIT]
            )
    for worker in old_result.get("worker_results", []) or []:
        if isinstance(worker, dict):
            gaps.extend(_strings(worker.get("gaps")))
            gaps.extend(_strings(worker.get("limitations")))
    error = old_result.get("error") or {}
    if error.get("code"):
        gaps.append(
            f"历史运行结果：{error['code']}；本次回放不表示该运行或其研究问题已修复。"
        )
    conclusion = (report or {}).get("conclusion")
    if isinstance(conclusion, str) and conclusion.strip():
        texts.append("### 历史已保存结论（未重新核验）\n\n" + conclusion)
        if len(conclusion) < 7900:
            conclusion = "历史已保存结论（未重新核验）：" + conclusion
        else:
            conclusion = "历史结论原文已完整保留在正文；此次回放没有形成新的投资判断。"
    else:
        conclusion = "历史文件没有保存独立投资判断；此次仅回放其已保存答复和材料，未生成新的投资结论。"
        gaps.append(
            "历史文件未保存独立 conclusion，不能将历史事实陈述重新包装为新投资判断。"
        )
    body = "**历史保存材料回放，未重新核验。**\n\n" + "\n\n".join(texts)
    gaps = list(dict.fromkeys(gaps))
    run.task_gaps[task_id] = gaps
    now = datetime.now(UTC)
    run.results[task_id] = TaskResult(
        task_id=task_id,
        status="partial",
        narrative="已保存材料见回放报告；此次没有执行新的研究。",
        started_at=now,
        ended_at=now,
    )
    run.judgment = Judgment(
        judgment_id="J1",
        conclusion=conclusion,
        finding_ids=list(run.ledger.findings),
        limitations=[_REPLAY_LIMIT],
    )
    run.report_draft = ReportDraft(
        sections=[body], conclusion=conclusion, limitations=[_REPLAY_LIMIT]
    )
    run.status = "partial"
    validate_ledger(run.ledger)
    validate_judgment(run.judgment, run.ledger)
    run.event(
        {
            "type": "offline_legacy_replay",
            "source_run_id": source_dir.name,
            "source_status": old_result.get("status"),
            "report_artifact": report_file,
        }
    )
    run.report_markdown = render_report(run, run.skill())
    result = {
        "run_id": run.run_id,
        "source_run_id": source_dir.name,
        "execution_mode": "offline_legacy_replay",
        "status": "partial",
        "source_status": old_result.get("status"),
        "source_error_code": error.get("code"),
        "calls": dict(budget.counts),
        "counts": {
            "sources": len(run.ledger.sources),
            "evidence": len(run.ledger.evidence),
            "findings": len(run.ledger.findings),
            "metrics": 0,
        },
        "gaps": gaps,
        "report_markdown": run.report_markdown,
    }
    if output is not None:
        result["output_path"] = str(output / run.run_id)
        destination = export_run(run, result, output)
        (destination / "replay.json").write_text(
            json.dumps(
                {
                    "input_directory": str(source_dir),
                    "report_artifact": report_file,
                    "legacy_evidence_spans": spans,
                    "limitations": [_REPLAY_LIMIT],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return result


def main(argv=None) -> int:
    """Replay a saved run, optionally exporting a fresh directory outside its input."""
    parser = argparse.ArgumentParser(
        description="Pure offline replay of saved legacy research."
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir")
    arguments = parser.parse_args(argv)
    try:
        result = replay_skill_report(arguments.run_dir, arguments.output_dir)
    except ResearchError as error:
        sys.stdout.write(
            json.dumps(
                {"status": "failed", "error": error.code, "message": str(error)},
                ensure_ascii=False,
            )
            + "\n"
        )
        return 1
    sys.stdout.write(
        json.dumps(
            {key: value for key, value in result.items() if key != "report_markdown"},
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
