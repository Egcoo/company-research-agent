"""Load the fixed Investment Master Skill and its mechanical execution adapter."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from research_agent.config import ConfigurationError

FIXED_SKILL_ID = "investment-master"


class AdditionalMethod(BaseModel):
    """Select sections from an additional upstream document."""

    model_config = ConfigDict(extra="forbid")
    file: str
    base_sections: list[str]
    task_sections: dict[str, list[str]]


class CoverageBinding(BaseModel):
    """Bind original methods to artifact kinds and existing report positions."""

    model_config = ConfigDict(extra="forbid")
    task_type: str
    method_sections: list[str] = Field(min_length=1)
    report_headings: list[str] = Field(min_length=1)
    materials: list[Literal["evidence", "findings", "metrics", "calculations"]]
    input_from: list[str] = Field(default_factory=list)


class RouteBinding(BaseModel):
    """Map a target route to existing documents, tools and presentation slots."""

    model_config = ConfigDict(extra="forbid")
    method_file: str
    base_sections: list[str]
    task_sections: dict[str, list[str]]
    report_section: str
    template_file: str
    report_attachments: dict[Literal["judgment", "metrics", "traceability"], str]
    capability_ids: list[str]
    subtype_parent: str | None
    subtype_sections: dict[str, str | None]
    additional_methods: dict[str, AdditionalMethod]
    coverage_bindings: list[CoverageBinding] = Field(default_factory=list)


class ExecutionAdapter(BaseModel):
    """Describe execution bindings, never investment methods or judgments."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    skill_id: Literal["investment-master"]
    snapshot_commit: str
    shared_sections: list[str]
    classification_sections: list[str]
    source_sections: list[str]
    report_source_sections: list[str]
    focused_heading: str = Field(min_length=1)
    routes: dict[str, RouteBinding]
    fingerprint: str = Field(default="", exclude=True)
    manifest_hash: str = Field(default="", exclude=True)


@dataclass(frozen=True)
class SkillBundle:
    """Expose selected original methods and program-owned execution bindings."""

    kind: str
    fund_type: str | None
    files: list[str]
    hashes: dict[str, str]
    methodology: str
    report_headings: list[str]
    source_url: str
    skill_id: str = FIXED_SKILL_ID
    adapter_hash: str = ""
    capability_ids: list[str] = field(default_factory=list)
    task_catalog: dict[str, list[str]] = field(default_factory=dict)
    report_attachments: dict[str, str] = field(default_factory=dict)
    focused_heading: str = "研究答复"
    requirements: list[dict] = field(default_factory=list)
    method_file: str = ""


def _root():
    return resources.files("research_agent").joinpath("skills", FIXED_SKILL_ID)


def _sections(text, headings, *, level=2):
    marker = "#" * level + " "
    chunks = re.split(rf"(?m)(?=^{re.escape(marker)})", text)
    by_heading = {
        chunk.splitlines()[0][len(marker) :].strip(): chunk.strip()
        for chunk in chunks
        if chunk.startswith(marker)
    }
    if any(heading not in by_heading for heading in headings):
        raise ConfigurationError("Pinned skill is missing a referenced method section.")
    return "\n\n".join(by_heading[heading] for heading in dict.fromkeys(headings))


def _read(root, manifest, name):
    if (
        name not in manifest["hashes"]
        or name.startswith("/")
        or ".." in name.split("/")
    ):
        raise ConfigurationError(
            "Adapter resource is outside the pinned file allowlist."
        )
    data = root.joinpath(*name.split("/")).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["hashes"][name]:
        raise ConfigurationError("Pinned skill resource failed its SHA256 check.")
    return data.decode("utf-8")


def load_adapter() -> ExecutionAdapter:
    """Validate the sole selected Skill before routing or any model request."""
    try:
        root = _root()
        manifest_data = root.joinpath("manifest.json").read_bytes()
        manifest = json.loads(manifest_data)
        data = root.joinpath("runtime_policy.json").read_bytes()
        adapter = ExecutionAdapter.model_validate_json(data)
        if adapter.snapshot_commit != manifest["commit"] or set(adapter.routes) != {
            "stock",
            "fund",
        }:
            raise ConfigurationError(
                "Adapter does not match the fixed Skill snapshot or its V2 target routes."
            )
        from research_agent.tools.research import CAPABILITY_METHODS

        main = _read(root, manifest, "SKILL.md")
        _sections(main, adapter.shared_sections + adapter.classification_sections)
        _sections(
            _read(root, manifest, "references/source-verification.md"),
            adapter.source_sections + adapter.report_source_sections,
        )
        for route in adapter.routes.values():
            if set(route.capability_ids) - set(CAPABILITY_METHODS):
                raise ConfigurationError(
                    "Adapter references an unregistered capability."
                )
            method = _read(root, manifest, route.method_file)
            _sections(method, route.base_sections)
            for headings in route.task_sections.values():
                _sections(method, headings)
            if route.subtype_parent:
                parent = _sections(method, [route.subtype_parent])
                for heading in route.subtype_sections.values():
                    if heading:
                        _sections(parent, [heading], level=3)
            contract = _sections(main, [route.report_section])
            headings = re.findall(r"(?m)^\d+\. `([^`]+)`", contract)
            if not headings or set(route.report_attachments.values()) - set(headings):
                raise ConfigurationError(
                    "Adapter presentation slots do not match the original report contract."
                )
            for binding in route.coverage_bindings:
                if binding.task_type not in route.task_sections or set(
                    binding.input_from
                ) - set(route.task_sections):
                    raise ConfigurationError(
                        "Coverage binding references an unknown responsibility."
                    )
                _sections(method, binding.method_sections)
                if set(binding.method_sections) - set(
                    route.base_sections + route.task_sections[binding.task_type]
                ):
                    raise ConfigurationError(
                        "Coverage method is outside the responsibility's selected sections."
                    )
                if set(binding.report_headings) - set(headings):
                    raise ConfigurationError(
                        "Coverage binding references an unknown report position."
                    )
            if route.coverage_bindings:
                expected_methods = {
                    section
                    for sections in route.task_sections.values()
                    for section in sections
                }
                bound_methods = {
                    section
                    for binding in route.coverage_bindings
                    for section in binding.method_sections
                }
                if expected_methods - bound_methods:
                    raise ConfigurationError(
                        "Coverage bindings omit an original research method."
                    )
            _read(root, manifest, route.template_file)
            for extra in route.additional_methods.values():
                if set(extra.task_sections) - set(route.task_sections):
                    raise ConfigurationError(
                        "Additional method references an unsupported responsibility."
                    )
                text = _read(root, manifest, extra.file)
                _sections(text, extra.base_sections)
                for headings in extra.task_sections.values():
                    _sections(text, headings)
        adapter.fingerprint = hashlib.sha256(data).hexdigest()
        adapter.manifest_hash = hashlib.sha256(manifest_data).hexdigest()
        return adapter
    except ConfigurationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        raise ConfigurationError(
            "The fixed Skill execution adapter is missing or invalid."
        ) from None


def classification_material(adapter):
    """Return original routing instructions without selecting another Skill."""
    root = _root()
    manifest_data = root.joinpath("manifest.json").read_bytes()
    if adapter.manifest_hash != hashlib.sha256(manifest_data).hexdigest():
        raise ConfigurationError("Skill snapshot manifest changed during the run.")
    manifest = json.loads(manifest_data)
    return _sections(_read(root, manifest, "SKILL.md"), adapter.classification_sections)


def load_skill(
    kind="stock", fund_type=None, task_type=None, focused=False, *, adapter=None
):
    """Route inside Investment Master; this function is not a multi-Skill selector."""
    adapter = adapter or load_adapter()
    if kind not in adapter.routes:
        raise ConfigurationError("Target route is not supported by the fixed Skill.")
    route = adapter.routes[kind]
    if fund_type is not None and fund_type not in route.subtype_sections:
        raise ConfigurationError("Subtype does not match the selected target route.")
    if task_type is not None and task_type not in route.task_sections:
        raise ConfigurationError("Task type does not match the selected route.")
    root = _root()
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (
        adapter.manifest_hash
        != hashlib.sha256(root.joinpath("manifest.json").read_bytes()).hexdigest()
    ):
        raise ConfigurationError("Skill snapshot manifest changed during the run.")
    loaded = {}

    def read(name):
        text = _read(root, manifest, name)
        loaded[name] = manifest["hashes"][name]
        return text

    main = read("SKILL.md")
    parts = [_sections(main, adapter.shared_sections)]
    report_headings = []
    if task_type is None and not focused:
        contract = _sections(main, [route.report_section])
        report_headings = re.findall(r"(?m)^\d+\. `([^`]+)`", contract)
        parts.append(contract)
    source_sections = adapter.source_sections + (
        adapter.report_source_sections if report_headings else []
    )
    parts.append(_sections(read("references/source-verification.md"), source_sections))
    method = read(route.method_file)
    headings = (
        route.base_sections + route.task_sections[task_type]
        if task_type
        else route.base_sections
        if focused
        else re.findall(r"(?m)^## (.+)$", method)
    )
    selected = _sections(method, headings)
    if route.subtype_parent and route.subtype_parent in headings:
        parent = _sections(method, [route.subtype_parent])
        subheading = route.subtype_sections.get(fund_type)
        selected = selected.replace(
            parent,
            "## "
            + route.subtype_parent
            + "\n\n"
            + _sections(parent, [subheading], level=3)
            if subheading
            else "",
        )
    parts.append(selected)
    extra = route.additional_methods.get(fund_type)
    if extra:
        text = read(extra.file)
        headings = (
            extra.base_sections + extra.task_sections.get(task_type, [])
            if task_type
            else extra.base_sections
            if focused
            else re.findall(r"(?m)^## (.+)$", text)
        )
        parts.append(_sections(text, headings))
    template_text = ""
    if report_headings:
        template_text = read(route.template_file)
        parts.append(template_text)
    return SkillBundle(
        kind,
        fund_type,
        list(loaded),
        loaded,
        "\n\n".join(part for part in parts if part),
        report_headings,
        f"{manifest['source_url']}/tree/{manifest['commit']}/{FIXED_SKILL_ID}",
        adapter_hash=adapter.fingerprint,
        capability_ids=list(route.capability_ids),
        task_catalog=dict(route.task_sections),
        report_attachments=dict(route.report_attachments),
        focused_heading=adapter.focused_heading,
        method_file=route.method_file,
        requirements=[
            {
                "ref": index + 1,
                **binding.model_dump(),
                "method_file": route.method_file,
                "method_hash": manifest["hashes"][route.method_file],
                "report_contract_ref": {
                    "file": "SKILL.md",
                    "section": route.report_section,
                    "hash": manifest["hashes"]["SKILL.md"],
                },
                "template_ref": {
                    "file": route.template_file,
                    "hash": manifest["hashes"][route.template_file],
                },
                "expected_report_parts": re.findall(
                    r"(?m)^### (.+)$",
                    _sections(
                        template_text,
                        [
                            f"{report_headings.index(heading) + 1}. {heading}"
                            for heading in binding.report_headings
                        ],
                    ),
                )
                if template_text
                else [],
                "additional_method_refs": [
                    {
                        "file": extra.file,
                        "sections": extra.base_sections
                        + extra.task_sections.get(binding.task_type, []),
                        "hash": manifest["hashes"][extra.file],
                    }
                ]
                if extra
                else [],
                "metric_names": [],
            }
            for index, binding in enumerate(route.coverage_bindings)
        ],
    )


def execution_contract(bundle, query, scope):
    """Bind the requested boundary without creating a second research policy."""
    return {
        "skill_id": bundle.skill_id,
        "adapter_hash": bundle.adapter_hash,
        "query": query,
        "scope": scope,
        "target_route": bundle.kind,
        "requirements": bundle.requirements if scope == "full" else [],
        "report_headings": bundle.report_headings or [bundle.focused_heading],
        "plan_gaps": [],
        "scope_preserved": True,
        "validation_scope": "registered_material_and_declared_coverage; semantics_not_proven",
    }
