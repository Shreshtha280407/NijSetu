"""Patient-level privacy policy: field-action defaults, age/geography rules,
date-shift range, and the k-anonymity review threshold.

Two documented presets ("balanced" / "strict") plus an optional JSON config
file that overrides individual knobs -- see POLICIES below for the actual
defaults and docs/PHASE2 for the documented contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..schema import FieldRole

MASK_SPAN = "MASK_SPAN"
TOKENIZE = "TOKENIZE"
GENERALIZE = "GENERALIZE"
SUPPRESS = "SUPPRESS"
RETAIN = "RETAIN"
SHIFT_DATE = "SHIFT_DATE"  # date-specific: value changes but is not removed

NATIONAL_SCHEMES = frozenset({"aadhaar", "abha_number", "abha_address", "pan"})


class PolicyError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AgeBandConfig:
    band_width: int = 10
    top_band_start: int = 90

    def band_for(self, age: int) -> str:
        if age < 0:
            age = 0
        if age >= self.top_band_start:
            return f"{self.top_band_start}+"
        lo = (age // self.band_width) * self.band_width
        hi = lo + self.band_width - 1
        return f"{lo}-{hi}"


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    date_shift_min_days: int = -30
    date_shift_max_days: int = 30
    age_band: AgeBandConfig = field(default_factory=AgeBandConfig)
    geography_level: str = "district"  # "district" | "state" | "none"
    k_threshold: int = 3
    provider_name_action: str = RETAIN
    facility_name_action: str = GENERALIZE
    version: str = "1"
    # A k<k_threshold group is always reported as a review trigger. Actually
    # HOLDING a patient for it is opt-in and deliberately off by default in
    # both presets -- the task is explicit that this must never auto-suppress
    # most of a small synthetic cohort. When enabled, a hold still requires a
    # genuinely rare clinical signature too (see risk.escalate_for_small_groups),
    # not group size alone.
    hold_distinctive_singletons: bool = False

    def role_action(self, role: FieldRole, scheme: str | None = None) -> str:
        """Default per-role action before any patient-level risk escalation
        (risk.apply_risk_actions may widen/suppress further for a specific
        patient's rare/small-group combination)."""
        if role in (FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME):
            return MASK_SPAN
        if role == FieldRole.PROVIDER_NAME:
            return self.provider_name_action
        if role == FieldRole.FACILITY_NAME:
            return self.facility_name_action
        if role == FieldRole.PATIENT_IDENTIFIER:
            return MASK_SPAN if scheme in NATIONAL_SCHEMES else TOKENIZE
        if role == FieldRole.CONTACT:
            return SUPPRESS
        if role == FieldRole.FINE_ADDRESS:
            return SUPPRESS
        if role == FieldRole.COARSE_GEOGRAPHY:
            return SUPPRESS if self.geography_level == "none" else RETAIN
        if role == FieldRole.DATE:
            return SHIFT_DATE
        if role in (FieldRole.CLINICAL_MEASUREMENT, FieldRole.CLINICAL_HISTORY):
            return RETAIN
        return RETAIN  # OTHER: no detected PII in this span -- pass through


BALANCED = PolicyConfig(
    name="balanced", date_shift_min_days=-30, date_shift_max_days=30,
    age_band=AgeBandConfig(band_width=10, top_band_start=90), geography_level="district",
    k_threshold=3, provider_name_action=RETAIN, facility_name_action=RETAIN,
)
STRICT = PolicyConfig(
    # geography_level="none": Phase 1's schema has no district->state hierarchy
    # (COARSE_GEOGRAPHY covers district/tehsil/block alike, with no bundled
    # mapping to the containing state), and guessing one from free text is
    # explicitly disallowed -- strict policy drops coarse geography entirely
    # rather than fabricate a state. See PHASE2_REPORT.md limitations.
    name="strict", date_shift_min_days=-45, date_shift_max_days=45,
    age_band=AgeBandConfig(band_width=20, top_band_start=80), geography_level="none",
    k_threshold=5, provider_name_action=SUPPRESS, facility_name_action=SUPPRESS,
)
POLICIES: dict[str, PolicyConfig] = {"balanced": BALANCED, "strict": STRICT}

_OVERRIDABLE_SCALARS = (
    "date_shift_min_days", "date_shift_max_days", "geography_level",
    "k_threshold", "provider_name_action", "facility_name_action",
    "hold_distinctive_singletons",
)


def load_policy(name: str, config_path: Path | None = None) -> PolicyConfig:
    base = POLICIES.get(name)
    if base is None:
        raise PolicyError("UNKNOWN_POLICY")
    if config_path is None:
        return base
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError:
        raise PolicyError("CONFIG_FILE_UNREADABLE") from None
    except json.JSONDecodeError:
        raise PolicyError("CONFIG_FILE_INVALID_JSON") from None
    if not isinstance(raw, dict):
        raise PolicyError("CONFIG_FILE_INVALID_JSON")

    overrides = {k: raw[k] for k in _OVERRIDABLE_SCALARS if k in raw}
    age_band = base.age_band
    if "age_band" in raw and isinstance(raw["age_band"], dict):
        ab = raw["age_band"]
        age_band = AgeBandConfig(
            band_width=int(ab.get("band_width", age_band.band_width)),
            top_band_start=int(ab.get("top_band_start", age_band.top_band_start)),
        )
    try:
        return replace(base, **overrides, age_band=age_band)
    except TypeError:
        raise PolicyError("CONFIG_FILE_UNKNOWN_KEY") from None
