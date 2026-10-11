"""Store acquired sources, source statements and research reasoning per run."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

from research_agent.config import ResearchError
from research_agent.schemas import (
    Calculation,
    CalculationOperation,
    EvidenceStatus,
    MetricObservation,
    ResearchEvidence,
    ResearchFinding,
    Source,
)


def _source_url(url: str) -> str:
    """Normalize fetched HTTP URLs without discarding content-changing queries."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Unsupported source URL")
        authority = parsed.netloc.lower()
        if (parsed.scheme, parsed.port) in {("http", 80), ("https", 443)}:
            authority = authority.rsplit(":", 1)[0]
        return urlunsplit(
            (parsed.scheme, authority, parsed.path or "/", parsed.query, "")
        )
    except (ValueError, TypeError):
        raise ResearchError(
            "invalid_source", "A fetched HTTP(S) source URL is required."
        ) from None


def _check_metric(evidence: ResearchEvidence, metric: MetricObservation) -> None:
    """Reject contradictions between explicitly recorded metric fields."""
    for field in ("unit", "currency", "reporting_period", "effective_date"):
        recorded = getattr(evidence, field)
        if recorded is not None and recorded != getattr(metric, field):
            raise ResearchError(
                "metric_mismatch", f"Metric {field} differs from its evidence."
            )
    if evidence.value is not None:
        try:
            recorded_value = Decimal(str(evidence.value))
        except InvalidOperation:
            raise ResearchError(
                "metric_mismatch", "Metric evidence does not contain a numeric value."
            ) from None
        if recorded_value != metric.value:
            raise ResearchError(
                "metric_mismatch", "Metric value differs from its evidence."
            )


def _arithmetic(
    operation: CalculationOperation, values: list[Decimal | str | int]
) -> tuple[list[Decimal], Decimal]:
    """Evaluate a bounded operation with finite Decimal operands."""
    try:
        operands = [Decimal(str(value)) for value in values]
        if not 2 <= len(operands) <= 20 or any(
            not value.is_finite() for value in operands
        ):
            raise ValueError("Invalid operands")
        result = operands[0]
        for value in operands[1:]:
            if operation == "add":
                result += value
            elif operation == "subtract":
                result -= value
            elif operation == "multiply":
                result *= value
            elif operation == "divide":
                result /= value
            else:
                raise ValueError("Unsupported operation")
        return operands, result
    except (InvalidOperation, ArithmeticError, ValueError):
        raise ResearchError(
            "invalid_calculation",
            "Calculation requires finite inputs and a supported operation without division by zero.",
        ) from None


class EvidenceLedger:
    """Own a thread-safe source and reasoning registry for one target and cutoff."""

    def __init__(self, as_of: date, target_key: str):
        """Create a fresh run registry without external storage or credentials."""
        if not target_key.strip():
            raise ResearchError("target_mismatch", "A research target key is required.")
        self.as_of = as_of
        self.target_key = target_key
        self.sources: dict[str, Source] = {}
        self.evidence: dict[str, ResearchEvidence] = {}
        self.metrics: dict[str, MetricObservation] = {}
        self.findings: dict[str, ResearchFinding] = {}
        self.calculations: dict[str, Calculation] = {}
        self._lock = threading.RLock()

    def add_source(
        self,
        url: str,
        title: str,
        text: str,
        source_type: str,
        published_at: date | None = None,
    ) -> Source:
        """Save a fetched snapshot, enriching only missing publication metadata."""
        normalized = _source_url(url)
        if published_at is not None and published_at > self.as_of:
            raise ResearchError(
                "future_source", "Source publication is after the research cutoff."
            )
        if not text.strip():
            raise ResearchError("empty_source", "Fetched source text cannot be empty.")
        with self._lock:
            existing = next(
                (
                    source
                    for source in self.sources.values()
                    if str(source.url) == normalized
                ),
                None,
            )
            if existing is not None:
                if existing.text != text:
                    raise ResearchError(
                        "source_changed",
                        "A fetched source changed during this run; the first snapshot is retained.",
                    )
                if (
                    existing.published_at is not None
                    and published_at is not None
                    and existing.published_at != published_at
                ) or existing.source_type != source_type:
                    raise ResearchError(
                        "source_changed",
                        "Source metadata changed during this run; the first snapshot is retained.",
                    )
                if existing.published_at is None and published_at is not None:
                    existing.published_at = published_at
                return existing.model_copy(deep=True)
            source = Source(
                source_id=f"S{len(self.sources) + 1}",
                target_key=self.target_key,
                url=normalized,
                title=title,
                retrieved_at=datetime.now(UTC),
                published_at=published_at,
                text=text,
                source_type=source_type,
                locations={
                    index: text[start : start + 6000]
                    for index, start in enumerate(range(0, len(text), 6000))
                },
            )
            self.sources[source.source_id] = source
            return source.model_copy(deep=True)

    def record(
        self,
        task_id: str,
        source_id: str,
        location: int,
        topic: str,
        statement: str,
        status: EvidenceStatus,
        definition: str,
        reporting_period: str | None = None,
        effective_date: date | None = None,
        value: str | Decimal | None = None,
        unit: str | None = None,
        currency: str | None = None,
    ) -> ResearchEvidence:
        """Bind a source statement to acquired content without accepting copied quotes."""
        with self._lock:
            source = self.sources.get(source_id)
            if source is None or location not in source.locations:
                raise ResearchError(
                    "unknown_source",
                    "Source or reading location was not acquired in this run.",
                )
            if effective_date is not None and effective_date > self.as_of:
                raise ResearchError(
                    "future_observation",
                    "Observation date is after the research cutoff.",
                )
            evidence = ResearchEvidence(
                evidence_id=f"E{len(self.evidence) + 1}",
                task_id=task_id,
                source_id=source.source_id,
                source_location=location,
                topic=topic,
                statement=statement,
                status=status,
                definition=definition,
                reporting_period=reporting_period,
                effective_date=effective_date,
                value=value,
                unit=unit,
                currency=currency,
            )
            self.evidence[evidence.evidence_id] = evidence
            return evidence.model_copy(deep=True)

    def add_metric(
        self,
        task_id: str,
        evidence_id: str,
        metric: str,
        value: Decimal | str | int | None = None,
        unit: str | None = None,
        definition: str | None = None,
        reporting_period: str | None = None,
        effective_date: date | None = None,
        currency: str | None = None,
    ) -> MetricObservation:
        """Record a numeric observation while inheriting source identity and status."""
        with self._lock:
            evidence = self.evidence.get(evidence_id)
            if evidence is None:
                raise ResearchError(
                    "unknown_evidence", "Metric references unregistered evidence."
                )
            if evidence.task_id != task_id:
                raise ResearchError(
                    "task_mismatch", "Metric task differs from its evidence task."
                )
            value = evidence.value if value is None else value
            unit = evidence.unit if unit is None else unit
            definition = evidence.definition if definition is None else definition
            if value is None or unit is None:
                raise ResearchError(
                    "metric_missing_fields",
                    "A metric requires a recorded or explicitly supplied value and unit.",
                )
            reporting_period = (
                reporting_period
                if reporting_period is not None
                else evidence.reporting_period
            )
            effective_date = (
                effective_date
                if effective_date is not None
                else evidence.effective_date
            )
            currency = currency if currency is not None else evidence.currency
            if effective_date is not None and effective_date > self.as_of:
                raise ResearchError(
                    "future_observation",
                    "Observation date is after the research cutoff.",
                )
            observation = MetricObservation(
                metric_id=f"M{len(self.metrics) + 1}",
                task_id=task_id,
                evidence_id=evidence_id,
                source_id=evidence.source_id,
                source_location=evidence.source_location,
                status=evidence.status,
                metric=metric,
                value=value,
                unit=unit,
                definition=definition,
                reporting_period=reporting_period,
                effective_date=effective_date,
                currency=currency,
            )
            _check_metric(evidence, observation)
            self.metrics[observation.metric_id] = observation
            return observation.model_copy(deep=True)

    def add_finding(
        self,
        task_id: str,
        topic: str,
        evidence_ids: list[str],
        inference: str,
        limitations: list[str],
    ) -> ResearchFinding:
        """Save reasoning with the selected acquired evidence and explicit limits."""
        with self._lock:
            if any(identifier not in self.evidence for identifier in evidence_ids):
                raise ResearchError(
                    "unknown_evidence", "Finding references unregistered evidence."
                )
            if not evidence_ids and not limitations:
                raise ResearchError(
                    "unsupported_finding",
                    "An unsourced finding must disclose its evidence limitation.",
                )
            finding = ResearchFinding(
                finding_id=f"F{len(self.findings) + 1}",
                task_id=task_id,
                topic=topic,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                inference=inference,
                limitations=limitations,
            )
            self.findings[finding.finding_id] = finding
            return finding.model_copy(deep=True)

    def add_calculation(
        self,
        task_id: str,
        operation: CalculationOperation,
        values: list[Decimal | str | int],
        currency: str | None = None,
    ) -> Calculation:
        """Compute finite Decimal arithmetic and retain all operands for replay."""
        operands, result = _arithmetic(operation, values)
        with self._lock:
            calculation = Calculation(
                calculation_id=f"C{len(self.calculations) + 1}",
                task_id=task_id,
                operation=operation,
                values=operands,
                result=result,
                currency=currency,
            )
            self.calculations[calculation.calculation_id] = calculation
            return calculation.model_copy(deep=True)
