"""Typed input/evidence representation shared by every pipeline stage.

These are the internal contracts referenced throughout Phase 1: source
coordinates, typed fields, detector evidence, identity features, and the
accepted/unresolved/conflicting link outcomes. Keeping them here (instead of
passing dicts around) is what lets adapters, detectors and the identity
resolver stay decoupled while still agreeing on offsets and roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class ValueType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    NULL = "null"
    ARRAY = "array"
    OBJECT = "object"


class FieldRole(str, Enum):
    """Semantic role assigned to a field or free-text span."""

    PATIENT_NAME = "PATIENT_NAME"
    RELATIVE_NAME = "RELATIVE_NAME"
    PROVIDER_NAME = "PROVIDER_NAME"
    FACILITY_NAME = "FACILITY_NAME"
    PATIENT_IDENTIFIER = "PATIENT_IDENTIFIER"
    CONTACT = "CONTACT"
    FINE_ADDRESS = "FINE_ADDRESS"
    COARSE_GEOGRAPHY = "COARSE_GEOGRAPHY"
    DATE = "DATE"
    CLINICAL_MEASUREMENT = "CLINICAL_MEASUREMENT"
    CLINICAL_HISTORY = "CLINICAL_HISTORY"
    OTHER = "OTHER"


class Disposition(str, Enum):
    """Processing outcome recorded for every discovered file/record."""

    PROCESSED = "PROCESSED"
    HELD = "HELD"
    PARSE_FAILED = "PARSE_FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class SourceCoordinate:
    """Points back at exactly where a value came from in the original input."""

    file_no: int
    file_path: str  # relative to the declared input root; never absolute/raw
    record_no: int  # 0-based row/resource/document ordinal within the file
    path: str  # field path / JSON pointer-ish / column name / line:col
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    line_no: Optional[int] = None

    def safe_label(self) -> str:
        """A label fit for logs/reports: no raw filename, no raw value."""
        return f"file#{self.file_no}:rec#{self.record_no}:{self.path}"


@dataclass
class Field:
    """One typed value read from a source, prior to any detection."""

    source: SourceCoordinate
    value_type: ValueType
    value: Any  # original, lossless value (Decimal for numbers, str, etc.)
    original_text: str  # exact original text span as it appeared in the source
    role: FieldRole = FieldRole.OTHER
    # Pre-assigned identifier scope, when the adapter already knows it
    # authoritatively (e.g. FHIR identifier.system) -- detection must not
    # overwrite this by re-inferring scheme/issuer from the header alias.
    issuer: Optional[str] = None
    scheme: Optional[str] = None


@dataclass
class DetectorEvidence:
    """One piece of evidence contributed by a detector rule."""

    rule_id: str
    tier: str  # e.g. "STRUCTURED_ANCHOR", "GAZETTEER", "CUE", "FORMAT", "CHECKSUM"
    detail_code: str  # safe, non-identifying reason code


@dataclass
class Feature:
    """A detected entity span plus the evidence that produced it."""

    source: SourceCoordinate
    role: FieldRole
    char_start: int
    char_end: int
    original_text: str  # kept only inside the private artifact
    evidence: list[DetectorEvidence] = field(default_factory=list)
    issuer: Optional[str] = None  # scheme/issuer scope for identifiers
    scheme: Optional[str] = None
    normalized_forms: dict[str, str] = field(default_factory=dict)
    confidence_tier: str = "LOW"  # LOW / MEDIUM / HIGH


@dataclass
class ScopedIdentifier:
    issuer: str
    scheme: str
    value: str
    source: SourceCoordinate


@dataclass
class PatientCandidateFeatures:
    """Compact per-record features used for blocking and scoring."""

    record_key: str  # file_no:record_no
    normalized_name_tokens: tuple[str, ...]
    skeletons: tuple[str, ...]
    vowel_collapsed: tuple[str, ...]
    birth_year: Optional[int]
    phone_digits: tuple[str, ...]
    address_tokens: tuple[str, ...]
    scoped_ids: tuple[ScopedIdentifier, ...]
    source_files: tuple[str, ...]


@dataclass
class LinkDecision:
    """Outcome of attaching (or not attaching) a record to a cluster."""

    record_key: str
    cluster_id: Optional[str]
    status: str  # ACCEPTED / HELD / CONFLICT
    reason_codes: list[str] = field(default_factory=list)
    evidence_tier: Optional[str] = None
    score: Optional[float] = None
    runner_up_margin: Optional[float] = None


@dataclass
class Action:
    """A planned (not yet applied) privacy action for Phase 2."""

    target: str  # safe target label, e.g. field path or feature id
    kind: str  # MASK_SPAN / TOKENIZE / GENERALIZE / SUPPRESS / RETAIN
    rule_id: str
    replacement_ref: Optional[str] = None
    confidence_tier: str = "LOW"


@dataclass
class FileRecord:
    """Per-file processing summary."""

    file_no: int
    file_path: str
    format: str
    disposition: Disposition
    record_count: Optional[int]  # None when parse failure makes count unknown
    reason_codes: list[str] = field(default_factory=list)


def to_value_type(value: Any) -> ValueType:
    if value is None:
        return ValueType.NULL
    if isinstance(value, bool):
        return ValueType.BOOLEAN
    if isinstance(value, int):
        return ValueType.INTEGER
    if isinstance(value, (float, Decimal)):
        return ValueType.DECIMAL
    if isinstance(value, list):
        return ValueType.ARRAY
    if isinstance(value, dict):
        return ValueType.OBJECT
    return ValueType.STRING
