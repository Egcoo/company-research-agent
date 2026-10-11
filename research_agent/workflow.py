"""Execute task-driven research with one reusable agent and a small explicit DAG."""

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4

from anthropic import APIConnectionError
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from pydantic import ConfigDict, Field, ValidationError, create_model, model_validator
from pydantic_core import PydanticCustomError

from research_agent.agents.planner import instructions as planning_instructions
from research_agent.agents.researcher import instructions as research_instructions
from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)
from research_agent.schemas import (
    METRIC_COLLECTION_ERROR,
    VERIFIED_COLLECTION_ERROR,
    EvidenceDraft,
    Judgment,
    PlanProposal,
    ReportDraft,
    RequirementGap,
    ResearchOutput,
    Target,
    TargetProposal,
    TaskDraft,
    TaskResult,
)
from research_agent.skill_loader import (
    FIXED_SKILL_ID,
    classification_material,
    execution_contract,
    load_adapter,
    load_skill,
)
from research_agent.tools import WebSearch
from research_agent.tools.market_data import market_data_coverage
from research_agent.tools.research import SourceTools, confirm_target_identity
from research_agent.tools.webpage import HttpFetcher
from research_agent.validation import (
    admit_plan,
    bind_tasks,
    material_index,
    missing_inputs,
    missing_metrics,
    report_requirement_checks,
    report_support_checks,
    requirement_coverage,
    validate_judgment,
    validate_ledger,
)


def _now():
    return datetime.now(UTC)


def _target_schema(known):
    """Validate unknown identity fields before accepting the bounded response."""

    def validate_identity(proposal):
        try:
            Target(
                **{
                    **proposal.model_dump(exclude={"scope"}),
                    **{key: value for key, value in known.items() if key != "scope"},
                }
            )
        except ValidationError:
            raise PydanticCustomError(
                "invalid_target_identity", "invalid_target_identity"
            ) from None
        return proposal

    return create_model(
        "TargetProposal",
        __config__=ConfigDict(extra="forbid", str_strip_whitespace=True),
        **{
            key: (
                Literal[None]
                if known.get("kind") == "stock" and key in {"fund_type", "share_class"}
                else field.annotation,
                copy.deepcopy(field),
            )
            for key, field in TargetProposal.model_fields.items()
            if key not in known
        },
        __validators__={
            "validate_identity": model_validator(mode="after")(validate_identity)
        },
    )


def _plan_schema(bundle, max_tasks, contract=None):
    """Expose adapter bindings and validate the DAG within the bounded correction."""
    task_refs_field = copy.deepcopy(TaskDraft.model_fields["requirement_refs"])
    gap_refs_field = copy.deepcopy(RequirementGap.model_fields["requirement_ref"])
    if contract and contract["scope"] == "full":
        refs = [item["ref"] for item in contract["requirements"]]
        task_refs_field.json_schema_extra = {"items": {"type": "integer", "enum": refs}}
        gap_refs_field.json_schema_extra = {"enum": refs}
    gap_schema = create_model(
        "RequirementGap",
        __base__=RequirementGap,
        requirement_ref=(int, gap_refs_field),
    )
    task_schema = create_model(
        "TaskDraft",
        __base__=TaskDraft,
        task_type=(
            Literal[tuple(bundle.task_catalog)],
            copy.deepcopy(TaskDraft.model_fields["task_type"]),
        ),
        requirement_refs=(list[int], task_refs_field),
        self_acquired_inputs=(
            list[Literal[tuple(bundle.task_catalog)]],
            copy.deepcopy(TaskDraft.model_fields["self_acquired_inputs"]),
        ),
        **{
            name: (
                list[Literal[tuple(bundle.capability_ids)]],
                copy.deepcopy(TaskDraft.model_fields[name]),
            )
            for name in ("capability_requests", "required_capabilities")
        },
    )

    def validate_dependencies(proposal):
        try:
            bind_tasks(proposal.tasks)
            if contract is not None:
                admit_plan(proposal, contract, bundle)
        except ResearchError as error:
            raise PydanticCustomError(
                error.code, error.code, error.details or {}
            ) from None
        return proposal

    def expose_focus_contract(schema):
        if contract and contract["scope"] == "focused":
            schema.setdefault("required", []).append("focus_requirements")
            schema["properties"]["focus_requirements"]["minItems"] = 1

    return create_model(
        "PlanProposal",
        __base__=PlanProposal,
        __config__=ConfigDict(json_schema_extra=expose_focus_contract),
        tasks=(list[task_schema], Field(min_length=1, max_length=max_tasks)),
        unavailable_requirements=(
            list[gap_schema],
            copy.deepcopy(PlanProposal.model_fields["unavailable_requirements"]),
        ),
        __validators__={
            "validate_dependencies": model_validator(mode="after")(
                validate_dependencies
            )
        },
    )


def _safe_schema_issues(error, schema):
    """Expose field paths and fixed reason codes, never model input or exception text."""
    source = getattr(error, "source", error)
    seen = set()
    while (
        source is not None
        and not isinstance(source, ValidationError)
        and id(source) not in seen
        and len(seen) < 8
    ):
        seen.add(id(source))
        source = source.__cause__ or (
            source.__context__ if not source.__suppress_context__ else None
        )
    if not isinstance(source, ValidationError):
        return [
            {
                "path": ["<root>"],
                "type": "missing_response" if error is None else "schema_error",
                "code": "structured_response_missing"
                if error is None
                else "schema_parse_error",
            }
        ]
    definition = schema.model_json_schema()
    fields = set(definition.get("properties", {}))
    for item in definition.get("$defs", {}).values():
        fields.update(item.get("properties", {}))
    reasons = {
        METRIC_COLLECTION_ERROR: "metric_collection_mismatch",
        VERIFIED_COLLECTION_ERROR: "verified_collection_mismatch",
    }
    issues = []
    for failure in source.errors(include_input=False, include_url=False)[:20]:
        reason = str(failure.get("ctx", {}).get("error", ""))
        issues.append(
            {
                "path": [
                    part if isinstance(part, int) or part in fields else "<extra>"
                    for part in failure["loc"]
                ]
                or ["<root>"],
                "type": failure["type"],
                "code": reasons.get(reason, failure["type"]),
                **{
                    key: value
                    for key, value in failure.get("ctx", {}).items()
                    if key
                    in {
                        "evidence_index",
                        "allowed_source_refs",
                        "allowed_locations",
                        "allowed_finding_indices",
                        "allowed_requirement_refs",
                        "missing_requirement_refs",
                        "task_index",
                    }
                    and (
                        type(value) is int
                        or isinstance(value, list)
                        and all(type(item) is int for item in value)
                    )
                },
            }
        )
    return issues


def _research_schema(requirement_refs, material_validator=None, metric_requests=()):
    """Correct invalid assessments before registering research output."""

    def validate_assessments(output):
        seen = set()
        for item in output.requirements:
            if (
                item.requirement_ref not in requirement_refs
                or item.requirement_ref in seen
            ):
                raise PydanticCustomError(
                    "invalid_requirement_reference", "invalid_requirement_reference"
                )
            seen.add(item.requirement_ref)
            if any(
                type(index) is not int or index < 0 or index >= len(output.findings)
                for index in item.finding_indices
            ):
                raise PydanticCustomError(
                    "unknown_finding",
                    "unknown_finding",
                    {"allowed_finding_indices": list(range(len(output.findings)))},
                )
            if item.status == "satisfied" and not item.finding_indices:
                raise PydanticCustomError(
                    "requirement_support_missing", "requirement_support_missing"
                )
        if material_validator:
            try:
                material_validator(output)
            except ResearchError as error:
                raise PydanticCustomError(
                    error.code, error.code, error.details or {}
                ) from None
        return output

    value_field = copy.deepcopy(EvidenceDraft.model_fields["value"])
    value_field.json_schema_extra = {
        "anyOf": [{"type": "number"}, {"type": "null"}],
        "description": "One scalar JSON number for one observation/period, without grouping commas, labels, ranges or year-value lists. Qualitative information belongs in statement; set value=null for it.",
    }
    evidence_schema = create_model(
        "EvidenceDraft",
        __base__=EvidenceDraft,
        value=(EvidenceDraft.model_fields["value"].annotation, value_field),
        **{
            "metric": (
                Literal[tuple(metric_requests)] | None,
                copy.deepcopy(EvidenceDraft.model_fields["metric"]),
            )
        }
        if metric_requests
        else {},
    )
    return create_model(
        "ResearchOutput",
        __base__=ResearchOutput,
        evidence=(
            list[evidence_schema],
            copy.deepcopy(ResearchOutput.model_fields["evidence"]),
        ),
        __validators__={
            "validate_assessments": model_validator(mode="after")(validate_assessments)
        },
    )


def _report_schema(index, *, section_count=None):
    """Share the existing correction allowance for invalid synthesis handles."""

    def expose_report_contract(schema):
        # Keep legacy parsing, but advertise only the single authored representation.
        schema["properties"].pop("sections", None)
        if "blocks" not in schema.setdefault("required", []):
            schema["required"].append("blocks")
        schema["properties"]["blocks"]["minItems"] = section_count or 1
        if section_count is not None:
            schema["properties"]["blocks"]["maxItems"] = section_count

    def normalize_single_section(data):
        if isinstance(data, dict) and section_count == 1:
            blocks = data.get("blocks")
            if (
                isinstance(blocks, list)
                and blocks
                and all(isinstance(block, dict) for block in blocks)
            ):
                data = {**data, "blocks": [blocks]}
        return data

    def validate_handles(draft):
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
            raise PydanticCustomError("unknown_support", "unknown_support")
        return draft

    return create_model(
        "ReportDraft",
        __base__=ReportDraft,
        __config__=ConfigDict(json_schema_extra=expose_report_contract),
        __validators__={
            "normalize_single_section": model_validator(mode="before")(
                normalize_single_section
            ),
            "validate_handles": model_validator(mode="after")(validate_handles),
        },
    )


class RunMiddleware(AgentMiddleware):
    """Share budget and safe errors without asking the model to copy state."""

    def __init__(self, run, stage, task_id=None, material_context=None):
        """Bind one invocation to its runtime stage and task."""
        self.run = run
        self.stage = stage
        self.task_id = task_id
        self.finalizing = False
        self.limits = run.task_call_limits.get(task_id)
        self.model_calls = 0
        self.tool_calls = 0
        self.material_context = material_context

    def _prepare_model_call(self, request):
        """Give each task its own closing calls and a current material index."""
        remaining = self.run.budget.remaining()
        reserve = 2 if self.task_id else 0
        seconds = (
            remaining["seconds"]
            - (self.run.synthesis_reserve_seconds if self.task_id else 0)
            - min(1.0, self.run.settings.deadline_seconds / 20)
        )
        if seconds <= 0:
            raise ResearchError(
                "synthesis_reserved", "Remaining time is reserved for synthesis."
            )
        local = None
        if self.limits is not None:
            local = {
                "model": self.limits["model"] - self.model_calls,
                "tool": max(0, self.limits["tool"] - self.tool_calls),
            }
            if local["model"] <= 0:
                raise ResearchError(
                    "task_budget_exhausted",
                    "The task's model call allocation is exhausted.",
                )
            closing_calls = 2 if self.limits["model"] >= 3 else 1
            closing = (
                local["model"] <= closing_calls
                or local["tool"] == 0
                or remaining["request"] == 0
                or seconds <= 30
            )
            if closing and not self.finalizing:
                self.run.event({"type": "task_closing", "task_id": self.task_id})
            self.finalizing |= closing
        self.run.budget.consume("model", reserve=reserve)
        self.model_calls += 1
        messages = list(request.messages)
        if self.task_id:
            control = {
                "remaining": remaining,
                "task_remaining": local,
                "reading_context": self.material_context()
                if self.material_context
                else [],
                "finalizing": self.finalizing,
                "action": (
                    "Return ResearchOutput now using the materials already read. Select relevant source statements "
                    "and explain their implications separately; preserve missing inputs as gaps. Do not return an empty object."
                    if self.finalizing
                    else "Research within the task allocation; return acquired evidence and findings without exhausting the budget."
                ),
            }
            for index, message in enumerate(messages):
                if isinstance(message, HumanMessage):
                    packet = json.loads(message.content)
                    packet["runtime_execution"] = control
                    messages[index] = message.model_copy(
                        update={
                            "content": json.dumps(
                                packet, ensure_ascii=False, default=str
                            )
                        }
                    )
                    break
            if self.finalizing:
                messages.append(
                    HumanMessage(
                        content="Acquisition is closed. Return the ResearchOutput tool response now, using the supplied reading_context and predecessor material. "
                        "Return traceable findings for what was obtained, and explicit gaps for the rest. Do not call retrieval or calculation tools."
                    )
                )
        return request.override(
            messages=messages,
            tools=[]
            if self.finalizing or self.task_id and remaining["model"] <= reserve + 1
            else request.tools,
            model_settings={
                **request.model_settings,
                **(
                    {"thinking": {"type": "disabled"}}
                    if self.run.settings.structured_thinking == "disabled"
                    and (self.stage != "research" or self.finalizing)
                    else {}
                ),
                "timeout": min(self.run.settings.model_timeout_seconds, seconds),
                "max_tokens": self.run.settings.max_output_tokens,
            },
        )

    def _record_response(self, response):
        """Keep safe completion diagnostics without storing model text or requests."""
        for message in response.result:
            if isinstance(message, AIMessage) and message.usage_metadata:
                self.run.record_usage(message.usage_metadata)
            if isinstance(message, AIMessage):
                reason = message.response_metadata.get("stop_reason")
                self.run.event(
                    {
                        "type": "model_response",
                        "stage": self.stage,
                        "task_id": self.task_id,
                        "stop_reason": reason
                        if reason
                        in {
                            "end_turn",
                            "tool_use",
                            "max_tokens",
                            "stop_sequence",
                            "pause_turn",
                            "refusal",
                        }
                        else None,
                        "output_tokens": (message.usage_metadata or {}).get(
                            "output_tokens"
                        ),
                        "content_blocks": [
                            {
                                "kind": block.get("type")
                                if block.get("type")
                                in {"text", "thinking", "tool_use", "redacted_thinking"}
                                else "other",
                                "characters": len(
                                    block.get("thinking", block.get("text", ""))
                                ),
                                **(
                                    {
                                        "input_characters": len(
                                            json.dumps(block.get("input"), default=str)
                                        ),
                                        "alternate_argument_fields": [
                                            key
                                            for key in (
                                                "partial_json",
                                                "arguments",
                                                "parameters",
                                            )
                                            if key in block
                                        ],
                                    }
                                    if block.get("type") == "tool_use"
                                    else {}
                                ),
                            }
                            for block in message.content
                            if isinstance(block, dict)
                        ]
                        if isinstance(message.content, list)
                        else [{"kind": "text", "characters": len(message.content)}],
                    }
                )
                for call in message.tool_calls:
                    self.run.record_model_fields(call["name"], call.get("args", {}))

    def wrap_model_call(self, request, handler):
        """Reserve synthesis calls and time before dispatching bounded model work."""
        response = handler(self._prepare_model_call(request))
        self._record_response(response)
        return response

    async def awrap_model_call(self, request, handler):
        """Apply the same call accounting to asynchronous agent execution."""
        response = await handler(self._prepare_model_call(request))
        self._record_response(response)
        return response

    def wrap_tool_call(self, request, handler):
        """Turn acquisition failures into visible gaps while preserving other tasks."""
        name = request.tool_call["name"]
        try:
            with self.run._lock:
                if self.finalizing:
                    raise ResearchError(
                        "task_acquisition_closed",
                        "Acquisition is closed; return ResearchOutput using already read material and explicit gaps.",
                    )
                if self.limits is not None and self.tool_calls >= self.limits["tool"]:
                    self.finalizing = True
                    raise ResearchError(
                        "task_tool_limit",
                        "Stop acquisition and return ResearchOutput using already read material.",
                    )
                self.tool_calls += 1
            self.run.budget.consume("tool")
            if (
                self.task_id
                and self.run.budget.remaining()["seconds"]
                <= self.run.synthesis_reserve_seconds
            ):
                raise ResearchError(
                    "synthesis_reserved",
                    "New research has stopped for final synthesis.",
                )
            result = handler(request)
            self.run.event(
                {"type": "tool_complete", "task_id": self.task_id, "tool": name}
            )
            return result
        except (ResearchError, ConfigurationError) as error:
            code = getattr(error, "code", "missing_configuration")
            self.run.add_gap(self.task_id, f"{name}: {code}")
            self.run.event(
                {
                    "type": "tool_error",
                    "task_id": self.task_id,
                    "tool": name,
                    "code": code,
                }
            )
            return ToolMessage(
                content=json.dumps(
                    {"error": code, "message": str(error)}, ensure_ascii=False
                ),
                tool_call_id=request.tool_call["id"],
                name=name,
            )


class ResearchRun:
    """Own one research run, its task context, material and lifecycle."""

    def __init__(
        self,
        settings: Settings,
        *,
        query="",
        as_of=None,
        ticker=None,
        target=None,
        scope=None,
        fetcher=None,
        search=None,
        target_type=None,
        fund_type=None,
        share_class=None,
        market=None,
    ):
        """Accept injected models and transports without reading credential files."""
        self.adapter = load_adapter()
        self.settings = settings
        self.run_id = uuid4().hex
        self.query = query
        self.as_of = as_of or date.today()
        if self.as_of > date.today():
            raise ConfigurationError("Research cutoff cannot be in the future.")
        self.ticker = ticker
        self.target = target
        self.scope = scope
        self.target_options = {
            "kind": target_type,
            "fund_type": fund_type,
            "share_class": share_class,
            "market": market,
        }
        self.budget = fetcher.budget if fetcher else ResearchBudget(settings)
        self.fetcher = fetcher or HttpFetcher(settings, self.budget)
        self.search = search or WebSearch(settings, self.budget)
        if self.search.budget is not self.budget:
            raise ValueError("Injected clients must share the run budget.")
        self.ledger = None
        self.tasks = []
        self.results = {}
        self.task_gaps = {}
        self.task_call_limits = {}
        self.events = []
        self.skill_manifest = {
            "selection": {
                "skill_id": FIXED_SKILL_ID,
                "adapter_hash": self.adapter.fingerprint,
                "snapshot_commit": self.adapter.snapshot_commit,
            }
        }
        self.execution_coverage = {}
        self.execution_contract = {}
        self.requirement_results = {}
        self.unavailable_tasks = {}
        self.runtime_warnings = {}
        self.support_index = {}
        self.judgment = None
        self.report_draft = None
        self.report_markdown = None
        self.checks = []
        self.status = "failed"
        self.token_usage = {}
        self.model_field_calls = []
        self.schema_diagnostics = []
        self.started = False
        self._lock = threading.RLock()

    @property
    def synthesis_reserve_seconds(self):
        """Use a fixed scope-based reserve, without reclaiming task allocations."""
        if self.scope == "focused":
            return min(20.0, self.settings.deadline_seconds / 5)
        return min(120.0, self.settings.deadline_seconds / 2)

    def close(self):
        """Release only clients owned by this run."""
        self.fetcher.close()
        self.search.close()

    def event(self, event):
        """Store safe execution events with program-generated timestamps."""
        with self._lock:
            self.events.append({**event, "timestamp": _now().isoformat()})

    def record_usage(self, usage):
        """Aggregate only provider-returned token counters."""
        with self._lock:
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    self.token_usage[key] = self.token_usage.get(key, 0) + value

    def record_model_fields(self, tool_name, arguments):
        """Measure field names and reference selections without logging model values."""
        refs = 0
        metadata = {
            "task_id",
            "source_id",
            "evidence_id",
            "finding_id",
            "run_id",
            "company",
            "target_identity",
            "agent_identity",
            "as_of",
            "status",
            "timestamp",
            "skill_path",
            "quote",
            "related_gaps",
        }
        repeated = []

        def visit(value):
            nonlocal refs
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        key
                        in {
                            "source_ref",
                            "evidence_ref",
                            "evidence_refs",
                            "location",
                            "evidence_indices",
                            "dependency_evidence_refs",
                            "support_refs",
                            "judgment_support_refs",
                            "requirement_refs",
                        }
                        and child is not None
                    ):
                        refs += len(child) if isinstance(child, list) else 1
                    if key in metadata and not (
                        key == "status"
                        and tool_name == "record_source_evidence"
                        or key == "status"
                        and tool_name == "ResearchOutput"
                        and isinstance(child, str)
                        and child in {"satisfied", "gap", "not_applicable"}
                    ):
                        repeated.append(key)
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(arguments)
        with self._lock:
            self.model_field_calls.append(
                {
                    "tool": tool_name,
                    "fields": sorted(arguments),
                    "material_reference_selections": refs,
                    "metadata_fields": repeated,
                }
            )

    def add_gap(self, task_id, message):
        """Retain gaps independently from model report wording."""
        with self._lock:
            gaps = self.task_gaps.setdefault(task_id or "run", [])
            if message not in gaps:
                gaps.append(message)

    def skill(self, task_type=None):
        """Load only the accepted route and responsibility, recording its manifest."""
        bundle = load_skill(
            self.target.kind,
            self.target.fund_type,
            task_type=task_type,
            focused=self.scope == "focused",
            adapter=self.adapter,
        )
        self.skill_manifest[task_type or "report"] = {
            "files": bundle.files,
            "hashes": bundle.hashes,
            "source_url": bundle.source_url,
            "skill_id": bundle.skill_id,
            "target_route": bundle.kind,
            "adapter_hash": bundle.adapter_hash,
        }
        return bundle

    def _structured(
        self,
        model,
        schema,
        prompt,
        packet,
        stage,
        *,
        tools=(),
        task_id=None,
        material_context=None,
    ):
        if stage == "synthesis":
            return self._report_json(model, schema, prompt, packet)
        repairs = 0
        middleware = RunMiddleware(self, stage, task_id, material_context)

        def repair(_error):
            nonlocal repairs
            repairs += 1
            middleware.finalizing = True
            issues = _safe_schema_issues(_error, schema)
            self.schema_diagnostics.append(
                {
                    "stage": stage,
                    "schema": schema.__name__,
                    "attempt": repairs,
                    "retry_scheduled": repairs <= 1,
                    "issues": issues,
                }
            )
            self.event(
                {
                    "type": "schema_repair" if repairs <= 1 else "schema_rejected",
                    "stage": stage,
                    "attempt": repairs,
                }
            )
            if repairs > 1:
                raise ResearchError(
                    "structured_output_invalid", "Structured correction limit reached."
                )
            details = "; ".join(
                ".".join(map(str, item["path"]))
                + ": "
                + item["code"]
                + " "
                + json.dumps(
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"path", "type", "code"}
                    }
                )
                for item in issues
            )
            protocol = (
                " task_type must use an exact task_catalog key from the selected route; describe the research topic in objective, "
                "not a new task type. Capability selections must use the declared capability_ids. "
                "Dependencies use zero-based plan indices, cannot reference self or missing tasks, and cannot form cycles. "
                "Runtime merges input_requirements dependencies with depends_on; required capabilities must be included "
                "in capability_requests when an explicit tool set is selected. "
                "A nonempty metric_names must use collection=metrics. For findings/evidence/calculations leave metric_names empty. "
                "verified_only is valid only for evidence or metrics. Preserve the intended dependency and data need; do not silently discard a required input."
                " Cover the execution_contract requirements or disclose unavailable_requirements with reasons; preserve full/focused scope. "
                "For focused research declare focus_requirements and requested metrics. Declare input_requirements for predecessor material, "
                "or self_acquired_inputs when this task will obtain it itself."
                if issubclass(schema, PlanProposal)
                else ""
            )
            if issubclass(schema, ResearchOutput):
                protocol += (
                    " Qualitative dates, fiscal labels, audit status and classifications belong in statement, not numeric metrics: use metric=null and value=null. "
                    "Named numeric metrics require a finite scalar value, unit and reporting_period or effective_date on the same evidence item. "
                    "For multiple years, create separate evidence items with one scalar and one period each. Do not put a year-value list or range in value. "
                    "Select source_ref and location only from reading_context; read_ref is a discovery handle, not source_ref. "
                    "Finding evidence_indices are zero-based within this output; dependency_evidence_refs must use supplied predecessor handles. "
                    "Do not attach predecessor financial facts to this task's new source. Reuse their supplied dependency_evidence_refs in findings. "
                    "If a numeric value is absent from the selected source, label it unresolved or retain it as arithmetic/inference; do not fabricate source support. "
                    "If the source was not successfully read, disclose the unavailable input as a gap instead of citing a discovered or failed source. "
                    "Do not discard required observations to fix the schema or invent missing context; preserve unavailable inputs as gaps."
                )
            if schema.__name__ == "TargetProposal":
                protocol += (
                    " Preserve all caller-provided identity fields. Stocks have fund_type=null and share_class=null. "
                    "Return a nonblank name; do not invent a fund subtype or share class for a stock."
                )
            if issubclass(schema, ReportDraft):
                protocol += (
                    " blocks is a nested array: one inner list of text blocks per supplied heading, in order. "
                    "For one focused heading, return blocks=[[{text, support_refs, kind}]], not one outer item per paragraph. "
                    "kind must be analysis, context or gap. Support selections must use the supplied support_ref integers."
                )
            return (
                "Correct only the requested structure using existing material. Fields: "
                + details
                + protocol
                + " Do not add runtime IDs, metadata or copied quotes."
            )

        agent = create_agent(
            model,
            system_prompt=prompt,
            tools=list(tools),
            response_format=ToolStrategy(schema, handle_errors=repair),
            middleware=[middleware],
        )
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(packet, ensure_ascii=False, default=str),
                    }
                ]
            },
            config={"recursion_limit": self.settings.max_model_calls * 4},
        )
        value = result.get("structured_response")
        if value is None and repairs == 0:
            repair(None)
            result = agent.invoke(
                {
                    "messages": [
                        *result["messages"],
                        HumanMessage(
                            content="Return the requested structured response using existing material only. Report unavailable inputs as gaps."
                        ),
                    ]
                },
                config={"recursion_limit": self.settings.max_model_calls * 4},
            )
            value = result.get("structured_response")
        if value is None:
            raise ResearchError(
                "structured_output_invalid",
                "No accepted structured response was returned.",
            )
        return schema.model_validate(value)

    def _report_json(self, model, schema, prompt, packet):
        """Validate report JSON directly, using the same budget and one correction."""
        middleware = RunMiddleware(self, "synthesis")
        system = SystemMessage(
            content=prompt
            + "\nRuntime delivery: return one JSON object matching the following schema. "
            "Put the actual report in conclusion and blocks, with escaped newlines in text strings. "
            "The runtime saves the file. Return JSON only, without tool calls, Markdown fences, ordinary prose or a duplicate sections field.\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        messages = [
            HumanMessage(content=json.dumps(packet, ensure_ascii=False, default=str))
        ]
        for attempt in range(2):
            request = ModelRequest(
                model=model, messages=messages, system_message=system, tools=[]
            )

            def invoke(prepared):
                response = prepared.model.bind(**prepared.model_settings).invoke(
                    [prepared.system_message, *prepared.messages]
                )
                return ModelResponse(result=[response])

            response = middleware.wrap_model_call(request, invoke).result[-1]
            try:
                # Preserve existing injected tool-response models; no tools execute here.
                calls = response.tool_calls
                if calls:
                    data = (
                        calls[0]["args"]
                        if len(calls) == 1 and calls[0]["name"] == schema.__name__
                        else None
                    )
                    draft = schema.model_validate(data)
                else:
                    content = response.content
                    if isinstance(content, list):
                        content = "\n".join(
                            item.get("text", "")
                            for item in content
                            if isinstance(item, dict) and item.get("type") == "text"
                        )
                    content = content.strip()
                    if content.startswith("```json\n") and content.endswith("```"):
                        content = content[len("```json\n") : -3].strip()
                    draft = schema.model_validate_json(content)
            except ValidationError as error:
                issues = _safe_schema_issues(error, schema)
                self.schema_diagnostics.append(
                    {
                        "stage": "synthesis",
                        "schema": schema.__name__,
                        "attempt": attempt + 1,
                        "retry_scheduled": attempt == 0,
                        "issues": issues,
                    }
                )
                self.event(
                    {
                        "type": "schema_repair" if attempt == 0 else "schema_rejected",
                        "stage": "synthesis",
                        "attempt": attempt + 1,
                    }
                )
                if attempt:
                    raise ResearchError(
                        "structured_output_invalid",
                        "Structured correction limit reached.",
                    ) from None
                messages = [
                    *messages,
                    *([] if calls else [response]),
                    HumanMessage(
                        content="Correct the complete report JSON using the existing material only. "
                        "Keep missing inputs as gaps. Escape newlines, quotes and backslashes inside strings. "
                        "Return only the JSON object. Issues: "
                        + json.dumps(issues, ensure_ascii=False)
                    ),
                ]
                continue
            if not calls:
                self.record_model_fields(schema.__name__, json.loads(content))
            return draft

    def identify(self, model):
        """Classify unknown targets; verified SEC identity is bound by the runtime."""
        if self.target is None:
            known = {
                key: value
                for key, value in self.target_options.items()
                if value is not None
            }
            if self.ticker:
                known["symbol"] = self.ticker
            if self.scope:
                known["scope"] = self.scope
            proposal_schema = _target_schema(known)
            proposal = self._structured(
                model,
                proposal_schema,
                "Identify the subject using the supplied Skill routing material and response schema. "
                "Return only unknown identity fields; do not invent missing identifiers or overwrite caller values. "
                "Use the supplied scope or distinguish a focused question from a full report request.\n"
                + classification_material(self.adapter),
                {
                    "question": self.query,
                    "symbol": self.ticker,
                    "provided": self.target_options,
                },
                "target",
            )
            data = {
                **proposal.model_dump(exclude={"scope"}),
                **{key: value for key, value in known.items() if key != "scope"},
            }
            data.update(
                {
                    key: value
                    for key, value in self.target_options.items()
                    if value is not None
                }
            )
            if self.ticker:
                data["symbol"] = self.ticker
            self.target = Target(**data)
            self.scope = self.scope or getattr(proposal, "scope", "full")
            try:
                self.target = confirm_target_identity(
                    self.target, self.settings, self.fetcher
                )
            except (ResearchError, ConfigurationError):
                self.add_gap(None, "目标尚未由适用的公开身份来源确认。")
        self.scope = self.scope or "full"
        if self.target.identity_status != "verified":
            self.add_gap(
                None, "目标身份或基金份额尚未核实；不得把候选身份当作已确认身份。"
            )
        from research_agent.evidence import EvidenceLedger

        self.ledger = EvidenceLedger(self.as_of, self.target.key)
        self.event(
            {
                "type": "target_classified",
                "kind": self.target.kind,
                "identity_status": self.target.identity_status,
            }
        )

    def plan(self, model):
        """Ask for research responsibilities instead of imposing fixed metrics."""
        bundle = self.skill()
        self.execution_contract = execution_contract(bundle, self.query, self.scope)
        planning_task_limit = self.settings.max_tasks
        if self.scope == "full":
            # Two acquisition steps, delivery and the existing bounded correction.
            available = max(0, self.budget.remaining()["model"] - 3)
            planning_task_limit = min(planning_task_limit, max(1, available // 4))
        proposal = self._structured(
            model,
            _plan_schema(bundle, planning_task_limit, self.execution_contract),
            planning_instructions(bundle.methodology),
            {
                "question": self.query,
                "target": self.target.model_dump(mode="json"),
                "scope": self.scope,
                "execution_contract": self.execution_contract,
                "task_catalog": bundle.task_catalog,
                "capability_ids": bundle.capability_ids,
                "capability_availability": SourceTools(
                    self.settings,
                    self.budget,
                    self.fetcher,
                    self.search,
                    self.ledger,
                    self.target,
                    "planning",
                ).capability_status(bundle.capability_ids),
                "max_tasks": planning_task_limit,
                "execution_budget": {
                    **self.budget.remaining(),
                    "synthesis_reserved_model_calls": 2,
                    "max_model_calls_per_task": 6,
                    "minimum_model_steps_for_acquisition_and_delivery": 4,
                },
                "source_coverage": market_data_coverage(),
            },
            "plan",
        )
        try:
            if len(proposal.tasks) > self.settings.max_tasks:
                raise ResearchError(
                    "invalid_plan", "Plan exceeds the configured task limit."
                )
            candidates, self.execution_contract = admit_plan(
                proposal, self.execution_contract, bundle
            )
            for task in candidates:
                self.skill(task.task_type)
                if set(task.capability_requests + task.required_capabilities) - set(
                    bundle.capability_ids
                ):
                    raise ResearchError(
                        "invalid_capability",
                        "Task capability is not declared by this route.",
                    )
        except (ResearchError, ConfigurationError) as error:
            code = getattr(error, "code", "invalid_plan_binding")
            self.schema_diagnostics.append(
                {
                    "stage": "plan",
                    "schema": "PlanProposal",
                    "attempt": 0,
                    "retry_scheduled": False,
                    "issues": [
                        {"path": ["tasks"], "type": "runtime_binding", "code": code}
                    ],
                }
            )
            self.event({"type": "plan_rejected", "code": code})
            raise
        self.tasks = candidates
        availability = SourceTools(
            self.settings,
            self.budget,
            self.fetcher,
            self.search,
            self.ledger,
            self.target,
            "planning",
        ).capability_status(bundle.capability_ids)
        unavailable = {
            item["id"]
            for item in availability
            if not (item["configured"] and item["applicable"])
        }
        for task in candidates:
            blocked = sorted(set(task.required_capabilities) & unavailable)
            if blocked:
                reason = "必要能力不可用：" + "、".join(blocked)
                self.unavailable_tasks[task.task_id] = reason
                existing = {
                    item["requirement_ref"]
                    for item in self.execution_contract["plan_gaps"]
                }
                self.execution_contract["plan_gaps"].extend(
                    {"requirement_ref": ref, "reason": reason}
                    for ref in task.requirement_refs
                    if ref not in existing
                )
                self.execution_contract["admission"] = "partial"
        for gap in self.execution_contract["plan_gaps"]:
            self.add_gap(
                None, f"计划未覆盖要求 {gap['requirement_ref']}：{gap['reason']}"
            )
        if self.execution_contract["plan_gaps"]:
            self.event(
                {
                    "type": "plan_degraded",
                    "scope": self.scope,
                    "gaps": len(self.execution_contract["plan_gaps"]),
                }
            )
        self.event({"type": "plan_accepted", "tasks": len(self.tasks)})

    def _execute_task(self, task, model):
        started = _now()
        self.event(
            {"type": "task_start", "task_id": task.task_id, "task_type": task.task_type}
        )
        narrative, code = "", None
        try:
            self.budget.check()
            if self.task_call_limits.get(task.task_id, {}).get("model", 2) < 2:
                raise ResearchError(
                    "task_budget_exhausted",
                    "Insufficient allocated calls for research and closing.",
                )
            bundle = self.skill(task.task_type)
            tools = SourceTools(
                self.settings,
                self.budget,
                self.fetcher,
                self.search,
                self.ledger,
                self.target,
                task.task_id,
                event_callback=self.event,
            )
            dependency_material = tools.dependency_material(task.dependencies)
            with self.ledger._lock:
                prior_findings = [
                    finding.model_dump(mode="json")
                    for finding in self.ledger.findings.values()
                    if finding.task_id in task.dependencies
                ]
                prior_metrics = tools.dependency_metrics(task.dependencies)
                prior_calculations = [
                    calculation.model_dump(mode="json")
                    for calculation in self.ledger.calculations.values()
                    if calculation.task_id in task.dependencies
                ]
            packet = {
                "objective": task.objective,
                "scope": self.scope,
                "required_outputs": task.required_outputs,
                "metric_requests": task.metric_requests,
                "requirements": [
                    item
                    for item in self.execution_contract.get("requirements", [])
                    if item["ref"] in task.requirement_refs
                ],
                "target": self.target.model_dump(mode="json"),
                "as_of": str(self.as_of),
                "question": self.query,
                "capabilities": tools.capability_status(bundle.capability_ids),
                "source_coverage": market_data_coverage(),
                "prior_results": [
                    self.results[identifier].model_dump(mode="json")
                    for identifier in task.dependencies
                ],
                "prior_findings": prior_findings,
                "prior_metrics": prior_metrics,
                "prior_calculations": prior_calculations,
                "dependency_evidence": dependency_material,
            }
            capability_ids = task.capability_requests or bundle.capability_ids
            status = tools.capability_status(capability_ids)
            unavailable = [
                item["id"]
                for item in status
                if item["id"] in task.required_capabilities
                and not (item["applicable"] and item["configured"])
            ]
            if unavailable:
                raise ResearchError(
                    "capability_unavailable",
                    "Required capability unavailable: " + ", ".join(unavailable),
                )
            output = self._structured(
                model,
                _research_schema(
                    task.requirement_refs, tools.validate_output, task.metric_requests
                ),
                research_instructions(task.task_type, bundle.methodology),
                packet,
                "research",
                tools=tools.tools(capability_ids),
                task_id=task.task_id,
                material_context=tools.reading_context,
            )
            tools.bind_output(output)
            self.requirement_results[task.task_id] = [
                item.model_dump(mode="json") for item in output.requirements
            ]
            narrative = output.summary
            for gap in output.gaps:
                self.add_gap(task.task_id, gap)
        except Exception as error:
            if isinstance(error, APIConnectionError):
                from research_agent.model_errors import _model_connection_error

                code = _model_connection_error(error)["code"]
            else:
                code = getattr(error, "code", "task_error")
            self.add_gap(task.task_id, f"任务未完成：{code}。已取得的材料保留。")
            self.event({"type": "task_error", "task_id": task.task_id, "code": code})
        with self.ledger._lock:
            findings = [
                item
                for item in self.ledger.findings.values()
                if item.task_id == task.task_id
            ]
            evidence = [
                item
                for item in self.ledger.evidence.values()
                if item.task_id == task.task_id
            ]
            metrics = [
                item
                for item in self.ledger.metrics.values()
                if item.task_id == task.task_id
            ]
            used_evidence = [
                self.ledger.evidence[identifier]
                for item in findings
                for identifier in item.evidence_ids
            ]
        gaps = list(self.task_gaps.get(task.task_id, []))
        missing = missing_metrics(task.metric_requests, metrics)
        if missing:
            gaps.append(
                "本任务所需的结构化指标尚未取得："
                + "、".join(missing)
                + "；不以其他指标替代。"
            )
        if not findings:
            gaps.append("本任务没有取得可追溯的研究发现。")
        if any(not item.evidence_ids or item.limitations for item in findings):
            gaps.append("部分研究推断仍有证据缺口或适用限制。")
        if any(item.status == "unresolved" for item in [*evidence, *used_evidence]):
            gaps.append("部分来源证据仍未核实。")
        status = (
            "partial"
            if findings and gaps
            else "success"
            if findings
            else "failed"
            if code
            else "unresolved"
        )
        result = TaskResult(
            task_id=task.task_id,
            status=status,
            narrative=narrative,
            gaps=gaps,
            started_at=started,
            ended_at=_now(),
            error_code=code,
        )
        self.event(
            {
                "type": "task_end",
                "task_id": task.task_id,
                "status": status,
                "evidence_count": len(evidence),
                "finding_count": len(findings),
            }
        )
        return result

    def research(self, model):
        """Execute ready tasks in bounded waves and isolate failed dependencies."""
        pending = {task.task_id: task for task in self.tasks}
        remaining = self.budget.remaining()
        available = max(0, remaining["model"] - 2)
        share, extra = divmod(available, len(self.tasks))
        tool_share, tool_extra = divmod(remaining["tool"], len(self.tasks))
        for index, task in enumerate(self.tasks):
            calls = min(6, share + (index < extra))
            if share < 2:
                calls = min(2, available) if available >= 2 else 0
                available -= calls
            self.task_call_limits[task.task_id] = {
                "model": calls,
                "tool": tool_share + (index < tool_extra),
            }
        while pending:
            ready = [
                task
                for task in pending.values()
                if all(identifier in self.results for identifier in task.dependencies)
            ]
            if not ready:
                raise ResearchError(
                    "invalid_dependency", "Research dependencies cannot be resolved."
                )
            runnable = []
            for task in ready:
                failed = [
                    identifier
                    for identifier in task.dependencies
                    if self.results[identifier].status in {"failed", "unresolved"}
                ]
                input_gaps = missing_inputs(task, self.tasks, self.ledger)
                if failed or input_gaps or task.task_id in self.unavailable_tasks:
                    now = _now()
                    self.results[task.task_id] = TaskResult(
                        task_id=task.task_id,
                        status="unresolved",
                        narrative="",
                        gaps=input_gaps
                        or [
                            self.unavailable_tasks.get(
                                task.task_id, "必要的前置研究不可用，未执行依赖分析。"
                            )
                        ],
                        started_at=now,
                        ended_at=now,
                        error_code="capability_unavailable"
                        if task.task_id in self.unavailable_tasks
                        else "dependency_unavailable",
                    )
                    self.event(
                        {
                            "type": "task_skipped",
                            "task_id": task.task_id,
                            "code": "dependency_unavailable",
                        }
                    )
                else:
                    runnable.append(task)
            with ThreadPoolExecutor(
                max_workers=self.settings.max_concurrent_tasks
            ) as pool:
                futures = {
                    pool.submit(
                        self._execute_task,
                        task,
                        model[task.task_type] if isinstance(model, dict) else model,
                    ): task
                    for task in runnable
                }
                for future in as_completed(futures):
                    task = futures[future]
                    self.results[task.task_id] = future.result()
            for task in ready:
                pending.pop(task.task_id)
        self.execution_coverage = self.coverage()
        # Classify acquisition warnings only after explicit, material-backed delivery.
        outcomes = {
            item["ref"]: item for item in self.execution_coverage["requirements"]
        }
        for task in self.tasks:
            result = self.results[task.task_id]
            assessments = self.requirement_results.get(task.task_id, [])
            delivered = {
                item["requirement_ref"]
                for item in assessments
                if item["status"] == "satisfied"
            }
            if (
                result.error_code
                or not set(task.requirement_refs).issubset(delivered)
                or any(
                    outcomes[ref]["status"] != "satisfied"
                    for ref in task.requirement_refs
                )
            ):
                continue
            warnings = [
                f"{event['tool']}: {event['code']}"
                for event in self.events
                if event["type"] == "tool_error"
                and event["task_id"] == task.task_id
                and (
                    event["code"].startswith("http_")
                    or event["code"] in {"transport_error", "search_error"}
                )
            ]
            if not warnings:
                continue
            self.runtime_warnings[task.task_id] = list(dict.fromkeys(warnings))
            result.gaps = [gap for gap in result.gaps if gap not in warnings]
            if not result.gaps:
                result.status = "success"
            self.event(
                {
                    "type": "task_delivery_resolved",
                    "task_id": task.task_id,
                    "status": result.status,
                    "warning_count": len(warnings),
                }
            )
        self.execution_coverage = self.coverage()

    def coverage(self):
        """Describe execution coverage; do not infer semantic completeness from prose."""
        bundle = self.skill()
        with self.ledger._lock:
            tasks = [
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "status": self.results[task.task_id].status,
                    "has_findings": any(
                        item.task_id == task.task_id
                        for item in self.ledger.findings.values()
                    ),
                    "missing_requested_metrics": missing_metrics(
                        task.metric_requests,
                        [
                            item
                            for item in self.ledger.metrics.values()
                            if item.task_id == task.task_id
                        ],
                    ),
                    "input_gaps": missing_inputs(task, self.tasks, self.ledger),
                }
                for task in self.tasks
            ]
        return {
            "skill_id": FIXED_SKILL_ID,
            "target_route": self.target.kind,
            "scope": self.scope,
            "expected_report_sections": len(bundle.report_headings) or 1,
            "tasks": tasks,
            "unplanned_responsibilities": sorted(
                set(bundle.task_catalog) - {task.task_type for task in self.tasks}
            ),
            "semantics_verified": False,
            "execution_contract": self.execution_contract,
            "runtime_warnings": self.runtime_warnings,
            "requirements": requirement_coverage(self),
            "coverage_scope": "declared tasks and registered artifacts; not a research-quality verdict",
        }

    def synthesize(self, model):
        """Build judgment from findings while the renderer binds provenance and state."""
        validate_ledger(self.ledger)
        if not self.ledger.findings:
            raise ResearchError(
                "research_unresolved",
                "No traceable findings were available for synthesis.",
            )
        failed_critical = [
            task
            for task in self.tasks
            if task.critical
            and self.results[task.task_id].status in {"failed", "unresolved"}
        ]
        if failed_critical and self.scope != "full":
            raise ResearchError(
                "critical_task_failed",
                "The explicit question's critical research could not be completed.",
            )
        if failed_critical:
            self.add_gap(
                None,
                "部分关键研究未完成；报告仅交付已取得的可追溯成果，未完成的投资判断仍须保留为缺口。",
            )
        bundle = self.skill()
        self.execution_coverage = self.coverage()
        self.support_index = material_index(self.ledger)
        packet = {
            "question": self.query,
            "target": self.target.model_dump(mode="json"),
            "as_of": str(self.as_of),
            "headings": bundle.report_headings or [bundle.focused_heading],
            "requirements": self.execution_coverage["requirements"],
            "support_materials": [
                {"support_ref": ref, "kind": item["kind"], "data": item["record"]}
                for ref, item in self.support_index.items()
            ],
            "sources": [
                {
                    "source_id": item.source_id,
                    "title": item.title,
                    "url": str(item.url),
                    "source_type": item.source_type,
                    "published_at": str(item.published_at)
                    if item.published_at
                    else None,
                }
                for item in self.ledger.sources.values()
            ],
            "tasks": [
                {"task_id": item.task_id, "status": item.status, "gaps": item.gaps}
                for item in self.results.values()
            ],
            "gaps": {
                key: [
                    gap
                    for gap in values
                    if gap not in self.runtime_warnings.get(key, [])
                ]
                for key, values in self.task_gaps.items()
            },
            "runtime_warnings": self.runtime_warnings,
        }
        self.report_draft = self._structured(
            model,
            _report_schema(
                self.support_index,
                section_count=len(bundle.report_headings) or 1,
            ),
            "Synthesize the requested report using the supplied original Skill output contract. "
            "Deliver one ReportDraft JSON object with the actual report in its fields; ordinary prose and thinking text do not deliver a report. "
            "The runtime writes the Markdown file. Do not save files or place the report outside the tool response. "
            "For a full company report, lead section one with the rating and decision table, then three reasons and three risks. "
            "Keep historical actuals, forecasts, assumptions and scenario valuation distinct. Use compact tables and connected analysis. "
            "Keep a full report's authored analysis within roughly 6000 Chinese characters (or a comparable concise answer in the requested language). "
            "Prioritize decision-relevant facts and implications; do not repeat every evidence statement, support record, limitation or the same table across sections. "
            "Section nine summarizes canonical evidence, dated source links, important gaps and disclaimer; do not repeat all findings or source documents. "
            "The runtime appends the canonical facts, source list and important gaps in section nine; provide only its concise disclaimer and source-priority explanation. "
            "Evidence is source content; findings are research inference; your conclusion is judgment with assumptions. "
            "Never promote unresolved material into verified facts; retain unavailable inputs and conditions declared in the material. "
            "A full report with unavailable critical requirements is a partial delivery: state which investment judgments cannot be made, "
            "rather than inventing valuation or purchase conclusions to fill the template. "
            "Return blocks in heading order: a few decisive text blocks selecting supplied support_refs, plus context or gap blocks. "
            "For a focused answer, use two to four concise decisive blocks plus genuinely relevant gaps; "
            "do not enumerate unrelated audits, sectors or investor-profile work that was not requested. "
            "blocks is a nested array with one inner list per supplied heading, not one outer item per paragraph. "
            "Runtime derives sections; do not return duplicate sections text. Select judgment_support_refs for the conclusion. "
            "Key numbers, valuation, reasons and risks need nearby support or explicit gaps. Calculations need input metrics or labelled assumptions. "
            "Use context only for background/disclaimers, never to bypass support for a decisive claim. "
            "Retain requirements marked gap; source opinions stay attributed. Do not repeat headings, URLs, citation numbers, IDs or exact quotes; "
            "the renderer binds source traceability and adds registered facts and gaps. "
            "Keep source opinions attributed and explain what would invalidate the judgment.\n"
            + bundle.methodology,
            packet,
            "synthesis",
        )
        if len(self.report_draft.sections) > self.settings.max_report_sections:
            raise ResearchError(
                "report_limit", "Report exceeds the configured structural limit."
            )
        self.execution_coverage = self.coverage()
        self.checks.extend(
            report_support_checks(self.report_draft, self.support_index, self.ledger)
        )
        self.checks.extend(
            report_requirement_checks(
                self.report_draft,
                self.support_index,
                self.execution_coverage["requirements"],
                bundle.report_headings or [bundle.focused_heading],
                self.tasks,
            )
        )
        self.judgment = Judgment(
            judgment_id="judgment-1",
            finding_ids=list(self.ledger.findings),
            **self.report_draft.model_dump(
                exclude={"sections", "blocks", "judgment_support_refs"}
            ),
        )
        validate_judgment(self.judgment, self.ledger)
        self.status = (
            "partial"
            if any(item.status != "success" for item in self.results.values())
            or any(
                gap not in self.runtime_warnings.get(key, [])
                for key, values in self.task_gaps.items()
                for gap in values
            )
            or any(
                item["status"] != "satisfied"
                for item in self.execution_coverage["requirements"]
            )
            or self.checks
            else "complete"
        )
        if self.report_draft.limitations:
            self.status = "partial"
        from research_agent.report import render_report

        self.report_markdown = render_report(self, bundle)
        self.event({"type": "report_accepted", "status": self.status})


def create_research_agent(run, *, model=None, task_model=None):
    """Expose the same scoped workflow to CLI, tests and Studio."""
    if model is None:
        if not run.settings.model.startswith("anthropic:"):
            raise ConfigurationError(
                "The configured model adapter currently supports Anthropic-compatible models only."
            )
        if run.settings.tracing:
            run.settings.require("langsmith")
        model = init_chat_model(
            run.settings.model,
            api_key=run.settings.require("anthropic"),
            temperature=0,
            max_retries=0,
            timeout=run.settings.model_timeout_seconds,
            max_tokens=run.settings.max_output_tokens,
        )

    def identify(state):
        if run.started:
            raise ResearchError(
                "run_already_started", "Create a fresh run for each research request."
            )
        run.started = True
        if not run.query:
            messages = state.get("messages", [])
            if not messages:
                raise ResearchError("query_missing", "A research question is required.")
            last = messages[-1]
            run.query = (
                last.get("content", "") if isinstance(last, dict) else last.content
            )
        run.identify(model)
        return {**state, "run_id": run.run_id}

    graph = StateGraph(dict)
    stages = {
        "target": identify,
        "plan": lambda state: (run.plan(model), state)[1],
        "research": lambda state: (run.research(task_model or model), state)[1],
        "synthesis": lambda state: (
            run.synthesize(model),
            {**state, "status": run.status, "report": run.report_markdown},
        )[1],
    }
    for name, function in stages.items():

        async def asynchronous(state, function=function):
            return await asyncio.to_thread(function, state)

        graph.add_node(name, RunnableLambda(function, afunc=asynchronous))
    graph.add_edge(START, "target")
    graph.add_edge("target", "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "synthesis")
    graph.add_edge("synthesis", END)
    return graph.compile(name="DeepEquity Skill Research")
