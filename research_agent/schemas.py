"""Define source statements, research reasoning and runtime-owned contracts."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

EvidenceStatus = Literal["verified", "source_opinion", "unresolved"]
TaskStatus = Literal["success", "partial", "failed", "unresolved"]
CalculationOperation = Literal["add", "subtract", "multiply", "divide"]
METRIC_COLLECTION_ERROR = "metric_names requires collection='metrics'."
VERIFIED_COLLECTION_ERROR = "verified_only applies only to evidence or metrics."


def _output_tool_schema(schema, fields, *, content_field=None):
    """Expose required wire fields while retaining saved-output defaults."""
    required = schema.setdefault("required", [])
    required.extend(name for name in fields if name not in required)
    if content_field:
        schema["properties"][content_field]["minItems"] = 1


class ResearchContract(BaseModel):
    """Reject unknown fields and empty whitespace at data boundaries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Target(ResearchContract):
    """Keep investment identity independent of any particular data provider."""

    kind: Literal["stock", "fund"]
    name: str = Field(min_length=1, max_length=300)
    symbol: str | None = Field(default=None, min_length=1, max_length=40)
    market: str | None = Field(default=None, min_length=1, max_length=80)
    fund_type: str | None = Field(default=None, min_length=1, max_length=80)
    share_class: str | None = Field(default=None, min_length=1, max_length=80)
    identity_status: Literal["verified", "unresolved"] = "unresolved"
    identity_source: str | None = Field(default=None, min_length=1, max_length=4096)
    adapter_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """Distinguish targets and fund share classes within a research run."""
        return ":".join(
            (
                self.kind,
                self.market or "",
                self.symbol or self.name.casefold(),
                self.share_class or "",
            )
        )

    @model_validator(mode="after")
    def validate_kind(self) -> "Target":
        """Reject fund-specific identity fields on a stock."""
        if self.kind == "stock" and (self.fund_type or self.share_class):
            raise ValueError("Stock identity cannot contain fund type or share class.")
        return self


class InputRequirement(ResearchContract):
    """Declare a prerequisite artifact using a plan entry, never a runtime ID."""

    dependency: int = Field(
        ge=0,
        strict=True,
        description="Zero-based predecessor plan entry. Runtime automatically adds this scheduling dependency.",
    )
    collection: Literal["evidence", "findings", "metrics", "calculations"] = Field(
        default="findings",
        description="Use metrics for numeric observations selected by metric_names; findings means interpretation, not metric values.",
    )
    metric_names: list[str] = Field(
        default_factory=list,
        max_length=30,
        description="Only valid with collection=metrics. Leave empty for evidence, findings or calculations.",
    )
    verified_only: bool = Field(
        default=False,
        description="Only valid for evidence or metrics. Research findings and calculations have no source-verification status.",
    )

    @model_validator(mode="after")
    def validate_collection_selector(self):
        """Reject conflicting selectors while structured output can still be corrected."""
        if self.metric_names and self.collection != "metrics":
            raise ValueError(METRIC_COLLECTION_ERROR)
        if self.verified_only and self.collection not in {"evidence", "metrics"}:
            raise ValueError(VERIFIED_COLLECTION_ERROR)
        return self


class TaskDraft(ResearchContract):
    """Capture planning choices without asking the model to generate IDs."""

    task_type: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=4000)
    required_outputs: list[str] = Field(default_factory=list, max_length=30)
    depends_on: list[int] = Field(
        default_factory=list,
        max_length=20,
        description="Additional zero-based predecessor plan indices. Runtime merges these with dependencies from input_requirements; required-input edges need not be repeated here.",
    )
    metric_requests: list[str] = Field(
        default_factory=list,
        max_length=30,
        description="Names of numeric observations only. Dates, fiscal labels, filing types and audit status belong in required_outputs as qualitative evidence.",
    )
    critical: bool = Field(
        default=False,
        description="True only when failure prevents any useful answer to the explicit question, including a partial answer. A full-scope requirement is not automatically critical.",
    )
    requirement_refs: list[int] = Field(default_factory=list, max_length=30)
    self_acquired_inputs: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Responsibility names from task_catalog, such as financial; never metric names, filenames or capability IDs. Use input_requirements for predecessor artifacts.",
    )
    capability_requests: list[str] = Field(default_factory=list, max_length=20)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    input_requirements: list[InputRequirement] = Field(
        default_factory=list, max_length=30
    )

    @field_validator("requirement_refs", mode="before")
    @classmethod
    def validate_requirement_handles(cls, values):
        """Keep task coverage references unambiguous at the planning boundary."""
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("Requirement references must be positive integers.")
        return values

    @field_validator("required_outputs", "metric_requests")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        """Require meaningful task requirements rather than blank items."""
        if any(not value.strip() for value in values):
            raise ValueError("Task requirements cannot be blank.")
        return [value.strip() for value in values]


class Task(TaskDraft):
    """Bind a planning item to runtime identity and explicit dependencies."""

    task_id: str = Field(min_length=1, max_length=80)
    dependencies: list[str] = Field(default_factory=list, max_length=20)


class Source(ResearchContract):
    """Preserve acquired text and program-generated reading locations."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    source_id: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    retrieved_at: datetime
    published_at: date | None = None
    text: str = Field(min_length=1)
    source_type: str = Field(min_length=1, max_length=80)
    locations: dict[int, str]

    @field_validator("source_id", "target_key", "title", "source_type")
    @classmethod
    def validate_labels(cls, value: str) -> str:
        """Normalize metadata without changing the source snapshot text."""
        if not value.strip():
            raise ValueError("Source metadata cannot be blank.")
        return value.strip()


class ResearchEvidence(ResearchContract):
    """Represent source statements separately from the researcher's inference."""

    evidence_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=300)
    statement: str = Field(min_length=1, max_length=6000)
    value: str | Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=120)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reporting_period: str | None = Field(default=None, min_length=1, max_length=300)
    effective_date: date | None = None
    definition: str = Field(min_length=1, max_length=2000)
    source_id: str = Field(min_length=1)
    source_location: int = Field(ge=0)
    status: EvidenceStatus


class MetricObservation(ResearchContract):
    """Keep a numeric observation's unit, time and definition explicit."""

    metric_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    metric: str = Field(min_length=1, max_length=300)
    value: Decimal = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=120)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reporting_period: str | None = Field(default=None, min_length=1, max_length=300)
    effective_date: date | None = None
    definition: str = Field(min_length=1, max_length=2000)
    source_id: str = Field(min_length=1)
    source_location: int = Field(ge=0)
    status: EvidenceStatus

    @model_validator(mode="after")
    def validate_time(self) -> "MetricObservation":
        """Require a reporting period or a dated point-in-time observation."""
        if self.reporting_period is None and self.effective_date is None:
            raise ValueError("A metric requires reporting_period or effective_date.")
        return self


class ResearchFinding(ResearchContract):
    """Preserve interpretation and limitations without relabeling it evidence."""

    finding_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=60)
    inference: str = Field(min_length=1, max_length=8000)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class Judgment(ResearchContract):
    """Bind a final conclusion to research findings and explicit conditions."""

    judgment_id: str = Field(min_length=1)
    conclusion: str = Field(min_length=1, max_length=8000)
    rationale: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    finding_ids: list[str] = Field(default_factory=list)


class TaskResult(ResearchContract):
    """Record task execution outcomes using runtime-owned identity and time."""

    task_id: str = Field(min_length=1)
    status: TaskStatus
    narrative: str = Field(default="", max_length=24000)
    gaps: list[str] = Field(default_factory=list, max_length=60)
    started_at: datetime
    ended_at: datetime
    error_code: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_times(self) -> "TaskResult":
        """Reject an execution end preceding its start."""
        if self.ended_at < self.started_at:
            raise ValueError("Task end cannot precede task start.")
        return self


class Calculation(ResearchContract):
    """Retain deterministic arithmetic inputs beside the computed result."""

    calculation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    operation: CalculationOperation
    values: list[Decimal] = Field(min_length=2, max_length=20)
    result: Decimal = Field(allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class TargetProposal(ResearchContract):
    """Request only identity information that the caller has not supplied."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["stock", "fund"]
    name: str = Field(min_length=1)
    symbol: str | None = None
    market: str | None = None
    fund_type: str | None = None
    share_class: str | None = None
    scope: Literal["full", "focused"] = "full"


class FocusRequirement(ResearchContract):
    """Describe a user-specific need without imposing a full research checklist."""

    task_type: str = Field(min_length=1)
    objective: str = Field(min_length=1, max_length=2000)
    materials: list[Literal["evidence", "findings", "metrics", "calculations"]] = Field(
        default_factory=lambda: ["evidence", "findings"], min_length=1, max_length=4
    )
    metric_names: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Names of necessary numeric observations only; describe required dates, fiscal labels and source identity in objective and request evidence.",
    )

    @field_validator("metric_names")
    @classmethod
    def validate_names(cls, values):
        """Require atomic nonblank observation names."""
        if any(not name.strip() for name in values):
            raise ValueError("Requested metric names cannot be blank.")
        return [name.strip() for name in values]


class RequirementGap(ResearchContract):
    """Disclose an unplanned requirement without changing the requested scope."""

    requirement_ref: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=2000)


class RequirementResult(RequirementGap):
    """Return an explicit research coverage assessment beside acquired material."""

    status: Literal["satisfied", "gap", "not_applicable"]
    finding_indices: list[int] = Field(
        default_factory=list,
        max_length=30,
        description="Zero-based indices in this response's findings array, not evidence/source handles. For three findings the valid indices are 0, 1, 2.",
    )

    @field_validator("finding_indices", mode="before")
    @classmethod
    def validate_finding_indices(cls, values):
        """Reject coerced or repeated output finding selections."""
        if any(type(value) is not int or value < 0 for value in values) or len(
            values
        ) != len(set(values)):
            raise ValueError("Finding indices must be distinct nonnegative integers.")
        return values


class PlanProposal(ResearchContract):
    """Describe research needs without generating runtime task identities."""

    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskDraft] = Field(min_length=1, max_length=8)
    focus_requirements: list[FocusRequirement] = Field(
        default_factory=list, max_length=20
    )
    unavailable_requirements: list[RequirementGap] = Field(
        default_factory=list, max_length=30
    )


class ReportBlock(ResearchContract):
    """Select runtime material handles beside a decisive piece of report prose."""

    text: str = Field(min_length=1, max_length=8000)
    support_refs: list[int] = Field(
        default_factory=list,
        max_length=40,
        description="Select the materials needed for the decisive claims in this block. A cited metric already carries its source evidence; avoid repeating that evidence selection.",
    )
    kind: Literal["analysis", "context", "gap"] = "analysis"
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    gap: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("support_refs", mode="before")
    @classmethod
    def validate_handles(cls, values):
        """Accept only positive, distinct material references."""
        if any(type(value) is not int or value < 1 for value in values) or len(
            values
        ) != len(set(values)):
            raise ValueError("Support references must be distinct positive integers.")
        return values


class ReportDraft(ResearchContract):
    """Return reasoning and prose, never runtime identities or copied quotations."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=lambda schema: _output_tool_schema(
            schema, ["blocks"], content_field="blocks"
        ),
    )
    sections: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="Legacy text representation. For new reports return blocks only; runtime derives sections.",
    )
    blocks: list[list[ReportBlock]] = Field(
        default_factory=list,
        max_length=64,
        description="Heading-order sections containing a few text blocks with runtime support_refs, assumptions or gaps.",
    )
    judgment_support_refs: list[int] = Field(default_factory=list, max_length=20)
    conclusion: str = Field(min_length=1)
    rationale: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("judgment_support_refs", mode="before")
    @classmethod
    def validate_judgment_handles(cls, values):
        """Reject coerced or repeated conclusion references."""
        return ReportBlock.validate_handles(values)

    @model_validator(mode="after")
    def bind_sections(self):
        """Derive compatible section text without asking the model to repeat prose."""
        if self.blocks:
            if any(not blocks for blocks in self.blocks):
                raise ValueError("Report sections cannot contain empty block lists.")
            derived = [
                "\n\n".join(block.text for block in blocks) for blocks in self.blocks
            ]
            if self.sections and self.sections != derived:
                raise ValueError("Legacy section text conflicts with report blocks.")
            self.sections = derived
        elif not self.sections:
            raise ValueError("Return report blocks or saved legacy sections.")
        return self


class EvidenceDraft(ResearchContract):
    """Select fetched content and describe its meaning without copying metadata."""

    source_ref: int | None = Field(
        default=None,
        ge=1,
        strict=True,
        description="Exact source_ref returned by read_source/reading_context, never the discovery read_ref. A sole source can be runtime-bound.",
    )
    location: int | None = Field(
        default=None,
        ge=0,
        strict=True,
        description="Exact location handle from that source's already read locations, not a line or table-row number. A sole read location can be runtime-bound.",
    )
    topic: str = Field(min_length=1, max_length=300)
    statement: str = Field(min_length=1, max_length=6000)
    source_status: EvidenceStatus
    definition: str = Field(min_length=1, max_length=2000)
    metric: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        description="Only numeric observations. For filing dates, quarter labels, audit status and other qualitative metadata use null.",
    )
    value: str | Decimal | None = Field(
        default=None,
        description="Qualitative values may be text. A named metric requires a finite number, unit, and reporting_period or effective_date.",
    )
    unit: str | None = Field(default=None, min_length=1, max_length=120)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reporting_period: str | None = Field(default=None, min_length=1, max_length=300)
    effective_date: date | None = None

    @model_validator(mode="after")
    def validate_metric_context(self):
        """Require context once when a selected statement is a numeric observation."""
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise PydanticCustomError(
                "metric_value_not_numeric", "metric_value_not_numeric"
            )
        if self.metric and self.value is not None:
            text = str(self.value).strip()
            if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
                text = text.replace(",", "")
            try:
                number = Decimal(text)
                if not number.is_finite():
                    raise InvalidOperation
                self.value = number
            except InvalidOperation:
                raise PydanticCustomError(
                    "metric_value_not_numeric", "metric_value_not_numeric"
                ) from None
        if self.metric and (
            self.value is None
            or self.unit is None
            or self.reporting_period is None
            and self.effective_date is None
        ):
            raise PydanticCustomError(
                "metric_context_missing", "metric_context_missing"
            )
        return self


class FindingDraft(ResearchContract):
    """Choose this output's evidence or runtime-supplied predecessor handles."""

    topic: str = Field(min_length=1, max_length=300)
    evidence_indices: list[int] = Field(default_factory=list, max_length=40)
    dependency_evidence_refs: list[int] = Field(default_factory=list, max_length=40)
    inference: str = Field(min_length=1, max_length=8000)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("evidence_indices", "dependency_evidence_refs", mode="before")
    @classmethod
    def validate_references(cls, values):
        """Reject negative or boolean references rather than fuzzy matching materials."""
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Material references must be nonnegative integers.")
        return values


class ResearchOutput(ResearchContract):
    """Return analysis once; runtime registers evidence, metrics and findings."""

    model_config = ConfigDict(
        json_schema_extra=lambda schema: _output_tool_schema(
            schema, ["evidence", "findings", "gaps", "requirements"]
        )
    )

    summary: str = Field(default="", max_length=24000)
    evidence: list[EvidenceDraft] = Field(default_factory=list, max_length=40)
    findings: list[FindingDraft] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=30)
    requirements: list[RequirementResult] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_research_result(self):
        """Reject an empty response without requiring invented facts or findings."""
        if not (self.summary or self.evidence or self.findings or any(self.gaps)):
            raise PydanticCustomError(
                "empty_research_output",
                "Return research material or explain the unavailable inputs.",
            )
        return self
