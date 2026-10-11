"""Check research coverage, material references and explicit numeric contracts."""

import copy
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from research_agent.config import ResearchError
from research_agent.evidence import EvidenceLedger, _arithmetic, _check_metric
from research_agent.schemas import Judgment, MetricObservation, Task, TaskDraft


def bind_tasks(drafts: list[TaskDraft]) -> list[Task]:
    """Bind required input edges and IDs, rejecting invalid dependency graphs."""
    if not drafts:
        raise ResearchError(
            "invalid_plan", "A research plan requires at least one task."
        )
    drafts = [
        draft.model_copy(
            update={
                "depends_on": [
                    *draft.depends_on,
                    *sorted(
                        {item.dependency for item in draft.input_requirements}
                        - set(draft.depends_on)
                    ),
                ]
            }
        )
        for draft in drafts
    ]
    for index, draft in enumerate(drafts):
        if any(
            requirement.metric_names and requirement.collection != "metrics"
            for requirement in draft.input_requirements
        ):
            raise ResearchError(
                "invalid_input_requirement",
                "Metric names only select metric observations.",
            )
        if any(
            requirement.verified_only
            and requirement.collection not in {"evidence", "metrics"}
            for requirement in draft.input_requirements
        ):
            raise ResearchError(
                "invalid_input_requirement",
                "Verified status belongs to source evidence and observations.",
            )
        if draft.capability_requests and set(draft.required_capabilities) - set(
            draft.capability_requests
        ):
            raise ResearchError(
                "invalid_capability",
                "Required capabilities must be in the selected tool set.",
            )
        if len(set(draft.depends_on)) != len(draft.depends_on) or any(
            dependency < 0 or dependency >= len(drafts) or dependency == index
            for dependency in draft.depends_on
        ):
            raise ResearchError(
                "invalid_dependency",
                "Task dependencies must name distinct other plan items.",
            )
    remaining = set(range(len(drafts)))
    done = set()
    while remaining:
        ready = {
            index for index in remaining if set(drafts[index].depends_on).issubset(done)
        }
        if not ready:
            raise ResearchError(
                "dependency_cycle", "Research task dependencies contain a cycle."
            )
        remaining -= ready
        done |= ready
    return [
        Task(
            **draft.model_dump(),
            task_id=f"T{index + 1}",
            dependencies=[f"T{dependency + 1}" for dependency in draft.depends_on],
        )
        for index, draft in enumerate(drafts)
    ]


def missing_inputs(task, tasks, ledger) -> list[str]:
    """Check declared artifact availability without interpreting the analysis prose."""
    missing = []
    with ledger._lock:
        for requirement in task.input_requirements:
            predecessor = tasks[requirement.dependency].task_id
            items = [
                item
                for item in getattr(ledger, requirement.collection).values()
                if item.task_id == predecessor
            ]
            if requirement.verified_only:
                items = [
                    item
                    for item in items
                    if getattr(item, "status", None) == "verified"
                ]
            if requirement.metric_names:
                observed = {" ".join(item.metric.split()).casefold() for item in items}
                missing.extend(
                    "必要前置指标未取得：" + name
                    for name in requirement.metric_names
                    if " ".join(name.split()).casefold() not in observed
                )
            elif not items:
                missing.append("必要前置材料未取得：" + requirement.collection)
    return missing


def validate_ledger(ledger: EvidenceLedger) -> None:
    """Check stored references and explicit field agreement without proving prose."""
    with ledger._lock:
        try:
            for registry in (
                ledger.sources,
                ledger.evidence,
                ledger.metrics,
                ledger.findings,
                ledger.calculations,
            ):
                for item in registry.values():
                    type(item).model_validate(item.model_dump())
        except ValidationError:
            raise ResearchError(
                "schema_inconsistency",
                "Stored research data violates its typed contract.",
            ) from None
        for identifier, source in ledger.sources.items():
            if identifier != source.source_id or source.target_key != ledger.target_key:
                raise ResearchError(
                    "target_mismatch",
                    "Source identity differs from its research registry.",
                )
            if source.published_at is not None and source.published_at > ledger.as_of:
                raise ResearchError(
                    "future_source", "Source publication is after the research cutoff."
                )
            expected = {
                index: source.text[start : start + 6000]
                for index, start in enumerate(range(0, len(source.text), 6000))
            }
            if source.locations != expected:
                raise ResearchError(
                    "source_changed",
                    "Reading locations no longer match the saved source text.",
                )
        for identifier, evidence in ledger.evidence.items():
            source = ledger.sources.get(evidence.source_id)
            if (
                identifier != evidence.evidence_id
                or source is None
                or evidence.source_location not in source.locations
            ):
                raise ResearchError(
                    "unknown_source",
                    "Evidence references an unregistered source location.",
                )
            if (
                evidence.effective_date is not None
                and evidence.effective_date > ledger.as_of
            ):
                raise ResearchError(
                    "future_observation",
                    "Observation date is after the research cutoff.",
                )
        for identifier, metric in ledger.metrics.items():
            evidence = ledger.evidence.get(metric.evidence_id)
            if identifier != metric.metric_id or evidence is None:
                raise ResearchError(
                    "unknown_evidence", "Metric references unregistered evidence."
                )
            if (
                metric.task_id != evidence.task_id
                or metric.source_id != evidence.source_id
                or metric.source_location != evidence.source_location
                or metric.status != evidence.status
            ):
                raise ResearchError(
                    "metric_mismatch",
                    "Metric identity or status differs from its evidence.",
                )
            if (
                metric.effective_date is not None
                and metric.effective_date > ledger.as_of
            ):
                raise ResearchError(
                    "future_observation",
                    "Observation date is after the research cutoff.",
                )
            _check_metric(evidence, metric)
        for identifier, finding in ledger.findings.items():
            if identifier != finding.finding_id or any(
                item not in ledger.evidence for item in finding.evidence_ids
            ):
                raise ResearchError(
                    "unknown_evidence", "Finding references unregistered evidence."
                )
            if not finding.evidence_ids and not finding.limitations:
                raise ResearchError(
                    "unsupported_finding",
                    "An unsourced finding must disclose its evidence limitation.",
                )
        for identifier, calculation in ledger.calculations.items():
            _, result = _arithmetic(calculation.operation, calculation.values)
            if identifier != calculation.calculation_id or calculation.result != result:
                raise ResearchError(
                    "calculation_mismatch",
                    "Stored arithmetic result differs from its recorded inputs.",
                )


def validate_judgment(judgment: Judgment, ledger: EvidenceLedger) -> None:
    """Require final judgment references to name findings acquired in this run."""
    if set(judgment.finding_ids) != set(ledger.findings):
        raise ResearchError(
            "unknown_finding",
            "Judgment must reference the runtime-bound available findings.",
        )
    if not judgment.finding_ids and not judgment.limitations:
        raise ResearchError(
            "unsupported_judgment",
            "An unsupported judgment must disclose its research limitations.",
        )


def missing_metrics(
    metric_requests: list[str], metrics: list[MetricObservation]
) -> list[str]:
    """Return requests without an exact metric match after case and space normalization."""
    observed = {" ".join(item.metric.split()).casefold() for item in metrics}
    return [
        requested
        for requested in metric_requests
        if " ".join(requested.split()).casefold() not in observed
    ]


def admit_plan(proposal, contract, bundle):
    """Bind applicable requirements and reject a structurally valid but incomplete plan."""
    accepted = copy.deepcopy(contract)
    if contract["scope"] == "focused":
        if not proposal.focus_requirements:
            raise ResearchError(
                "focused_requirements_missing",
                "Declare the user-specific research requirements.",
            )
        accepted["requirements"] = []
        for index, goal in enumerate(proposal.focus_requirements, 1):
            if goal.task_type not in bundle.task_catalog:
                raise ResearchError(
                    "invalid_requirement_reference",
                    "Focused responsibility is outside the selected Skill.",
                )
            basis = next(
                (
                    item
                    for item in bundle.requirements
                    if item["task_type"] == goal.task_type
                ),
                {
                    "method_file": bundle.method_file,
                    "method_hash": bundle.hashes.get(bundle.method_file),
                },
            )
            accepted["requirements"].append(
                {
                    **basis,
                    **goal.model_dump(),
                    "ref": index,
                    "method_sections": bundle.task_catalog[goal.task_type],
                    "report_headings": [bundle.focused_heading],
                    "expected_report_parts": [],
                    "input_from": basis.get("input_from", []),
                    "materials": list(
                        dict.fromkeys(
                            [
                                *goal.materials,
                                *(["metrics"] if goal.metric_names else []),
                            ]
                        )
                    ),
                }
            )
    requirements = {item["ref"]: item for item in accepted["requirements"]}
    gaps = {
        item.requirement_ref: item.reason for item in proposal.unavailable_requirements
    }
    if len(gaps) != len(proposal.unavailable_requirements) or set(gaps) - set(
        requirements
    ):
        raise ResearchError(
            "invalid_requirement_reference",
            "Unavailable items must select distinct applicable requirements.",
            details={"allowed_requirement_refs": sorted(requirements)},
        )
    tasks = bind_tasks(proposal.tasks)
    covered = set()
    for task_index, task in enumerate(tasks):
        refs = task.requirement_refs or [
            ref
            for ref, item in requirements.items()
            if item["task_type"] == task.task_type
        ]
        if (
            not refs
            or len(refs) != len(set(refs))
            or any(
                ref not in requirements
                or requirements[ref]["task_type"] != task.task_type
                for ref in refs
            )
        ):
            raise ResearchError(
                "invalid_requirement_reference",
                "Each task must cover applicable requirements of its responsibility.",
                details={
                    "task_index": task_index,
                    "allowed_requirement_refs": [
                        ref
                        for ref, item in requirements.items()
                        if item["task_type"] == task.task_type
                    ],
                },
            )
        if not task.required_outputs:
            raise ResearchError(
                "missing_required_outputs",
                "Declare the material each research task must deliver.",
            )
        task.requirement_refs = refs
        covered.update(refs)
        if set(task.self_acquired_inputs) - set(bundle.task_catalog):
            raise ResearchError(
                "invalid_input_requirement",
                "Self-acquired input must use declared responsibilities.",
            )
        for ref in refs:
            requirement = requirements[ref]
            selected_metrics = {
                " ".join(name.split()).casefold() for name in task.metric_requests
            } | {
                " ".join(name.split()).casefold()
                for item in task.input_requirements
                for name in item.metric_names
            }
            if {
                " ".join(name.split()).casefold()
                for name in requirement.get("metric_names", [])
            } - selected_metrics:
                raise ResearchError(
                    "missing_metric_requirement",
                    "The requested metrics have no declared acquisition or prerequisite.",
                )
            for predecessor_type in requirement.get("input_from", []):
                predecessors = [
                    index
                    for index, other in enumerate(tasks)
                    if other.task_type == predecessor_type
                    and other.task_id != task.task_id
                ]
                if (
                    predecessors
                    and predecessor_type not in task.self_acquired_inputs
                    and not any(
                        item.dependency in predecessors
                        for item in task.input_requirements
                    )
                ):
                    raise ResearchError(
                        "missing_input_requirement",
                        "Declare the required predecessor artifacts rather than an ordering edge alone.",
                    )
    missing = set(requirements) - covered - set(gaps)
    if missing:
        raise ResearchError(
            "plan_incomplete",
            "The plan omits applicable research requirements.",
            details={"missing_requirement_refs": sorted(missing)},
        )
    accepted["plan_gaps"] = [
        {"requirement_ref": ref, "reason": reason} for ref, reason in gaps.items()
    ]
    accepted["planned_tasks"] = {
        str(ref): [task.task_id for task in tasks if ref in task.requirement_refs]
        for ref in requirements
    }
    accepted["admission"] = "partial" if gaps else "accepted"
    return tasks, accepted


def requirement_coverage(run):
    """Measure registered delivery against declared requirements without certifying prose."""
    contract = run.execution_contract or {}
    declared_gaps = {
        item["requirement_ref"]: item["reason"]
        for item in contract.get("plan_gaps", [])
    }
    output = []
    for requirement in contract.get("requirements", []):
        ref = requirement["ref"]
        assigned = [task for task in run.tasks if ref in task.requirement_refs]
        problems = [declared_gaps[ref]] if ref in declared_gaps else []
        completed = False
        for task in assigned:
            result = run.results.get(task.task_id)
            if result is None:
                continue
            assessment = next(
                (
                    item
                    for item in run.requirement_results.get(task.task_id, [])
                    if item["requirement_ref"] == ref
                ),
                None,
            )
            with run.ledger._lock:
                findings = [
                    item
                    for item in run.ledger.findings.values()
                    if item.task_id == task.task_id
                ]
                if assessment:
                    indices = assessment.get("finding_indices", [])
                    if any(
                        type(index) is not int or index < 0 or index >= len(findings)
                        for index in indices
                    ):
                        problems.append("研究要求引用了不存在的发现。")
                        continue
                    findings = [findings[index] for index in indices]
                evidence_ids = {
                    identifier for item in findings for identifier in item.evidence_ids
                }
                evidence = [
                    item
                    for item in run.ledger.evidence.values()
                    if item.task_id == task.task_id or item.evidence_id in evidence_ids
                ]
                metric_tasks = {task.task_id, *task.dependencies}
                metrics = [
                    item
                    for item in run.ledger.metrics.values()
                    if item.task_id in metric_tasks and item.status != "unresolved"
                ]
                material = {
                    "findings": findings,
                    "evidence": [
                        item for item in evidence if item.status != "unresolved"
                    ],
                    "metrics": metrics,
                    "calculations": [
                        item
                        for item in run.ledger.calculations.values()
                        if item.task_id in metric_tasks
                    ],
                }
            absent = [kind for kind in requirement["materials"] if not material[kind]]
            missing = missing_metrics(requirement.get("metric_names", []), metrics)
            if absent or missing:
                problems.append("必要材料未取得：" + "、".join([*absent, *missing]))
            elif assessment and assessment["status"] != "satisfied":
                problems.append(assessment["reason"])
            elif contract.get("scope") == "full" and (not assessment or not findings):
                problems.append("完整研究缺少该要求的支持记录。")
            elif result.status in {"failed", "unresolved"}:
                problems.extend(result.gaps or ["必要研究未完成。"])
            else:
                completed = True
        if not completed and not problems:
            problems.append("本次适用要求尚未交付。")
        output.append(
            {
                **requirement,
                "task_ids": [task.task_id for task in assigned],
                "status": "satisfied"
                if completed and ref not in declared_gaps
                else "gap",
                "gaps": list(dict.fromkeys(problems))
                if not completed or ref in declared_gaps
                else [],
            }
        )
    return output


def material_index(ledger):
    """Assign synthesis-local short handles to existing registered artifacts."""
    index = {}
    with ledger._lock:
        for kind in ("evidence", "findings", "metrics", "calculations"):
            for item in getattr(ledger, kind).values():
                index[len(index) + 1] = {
                    "kind": kind,
                    "record": item.model_dump(mode="json"),
                }
    return index


def support_evidence(entry, ledger):
    """Resolve source provenance using existing relationships, never generated quotes."""
    kind, item = entry["kind"], entry["record"]
    if kind == "evidence":
        return [ledger.evidence[item["evidence_id"]]]
    if kind == "metrics":
        return [ledger.evidence[item["evidence_id"]]]
    if kind == "findings":
        return [ledger.evidence[identifier] for identifier in item["evidence_ids"]]
    return []


def report_support_checks(draft, index, ledger):
    """Check short references and arithmetic inputs while preserving semantic limits."""
    checks = []
    refs = [
        *draft.judgment_support_refs,
        *[
            ref
            for blocks in draft.blocks
            for block in blocks
            for ref in block.support_refs
        ],
    ]
    if any(type(ref) is not int or ref not in index for ref in refs):
        raise ResearchError(
            "unknown_support", "Report references material not supplied to synthesis."
        )
    if not draft.blocks:
        checks.append({"code": "legacy_report_support_unavailable"})
    if not draft.judgment_support_refs:
        checks.append({"code": "judgment_support_missing"})
    for section, blocks in enumerate(draft.blocks):
        for position, block in enumerate(blocks):
            codes = []
            entries = [index[ref] for ref in block.support_refs]
            evidence = [
                item for entry in entries for item in support_evidence(entry, ledger)
            ]
            if block.gap or block.kind == "gap":
                codes.append("declared_report_gap")
            if block.kind == "analysis" and not evidence:
                codes.append("decisive_support_missing")
            if any(item.status == "unresolved" for item in evidence):
                codes.append("unresolved_report_support")
            if any(
                entry["kind"] == "findings" and entry["record"]["limitations"]
                for entry in entries
            ):
                codes.append("limited_report_support")
            known_values = set()
            for item in evidence:
                if item.value is not None:
                    try:
                        known_values.add(Decimal(str(item.value)))
                    except InvalidOperation:
                        pass
            known_values.update(
                Decimal(entry["record"]["value"])
                for entry in entries
                if entry["kind"] == "metrics"
            )
            for entry in entries:
                if entry["kind"] == "calculations" and not block.assumptions:
                    inputs = {Decimal(value) for value in entry["record"]["values"]}
                    if inputs - known_values:
                        codes.append("calculation_inputs_untraced")
            checks.extend(
                {"code": code, "section": section, "block": position}
                for code in dict.fromkeys(codes)
            )
    if draft.judgment_support_refs and not any(
        support_evidence(index[ref], ledger) for ref in draft.judgment_support_refs
    ):
        checks.append({"code": "judgment_support_missing"})
    for ref in draft.judgment_support_refs:
        if any(
            item.status == "unresolved" for item in support_evidence(index[ref], ledger)
        ):
            checks.append({"code": "unresolved_judgment_support"})
        if index[ref]["kind"] == "findings" and index[ref]["record"]["limitations"]:
            checks.append({"code": "limited_judgment_support"})
    return checks


def report_requirement_checks(draft, index, requirements, headings, tasks):
    """Check report placement against applicable responsibility bindings."""
    checks = []
    if not draft.blocks or len(draft.blocks) != len(headings):
        return checks
    for requirement in requirements:
        if requirement["status"] != "satisfied":
            continue
        owners = set(requirement["task_ids"])
        owners.update(
            identifier
            for task in tasks
            if task.task_id in owners
            for identifier in task.dependencies
        )
        for heading in requirement["report_headings"]:
            if heading not in headings:
                continue
            selected = [
                index[ref]["record"]
                for block in draft.blocks[headings.index(heading)]
                for ref in block.support_refs
            ]
            if not any(item["task_id"] in owners for item in selected):
                checks.append(
                    {
                        "code": "requirement_report_support_missing",
                        "requirement_ref": requirement["ref"],
                        "heading": heading,
                    }
                )
    return checks
