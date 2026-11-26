"""Per-patient-cluster context computed once before per-record transform:
the release token, the date-shift offset, and (when derivable without
guessing) the age band at a known reference clinical event.

Every record a Phase 1 LinkDecision ever registers carries a cluster_id --
including an unresolved SINGLETON or an explicitly HELD record, whose
cluster_id is simply its own root. A standalone cluster (size 1) still gets
its own stable token and is sanitized as its own independent research
identity, per the blueprint: unresolved identity must not mean quarantine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schema import FieldRole
from . import keys
from .dates import DateParseError, age_years_at, parse_flexible_date
from .policy import PolicyConfig

_REFERENCE_EVENT_SCHEMES = ("admission_date", "encounter_date", "specimen_date", "report_date", "discharge_date")


@dataclass(frozen=True)
class PatientContext:
    cluster_id: str
    canonical_key: str
    patient_token: str
    date_shift_days: int
    reference_event_date: object | None  # datetime.date | None
    age_band: str | None
    age_band_reason: str  # "DERIVED" | "DERIVED_FROM_STATED_AGE" | "NO_DOB" | "NO_REFERENCE_EVENT" | "DOB_UNPARSEABLE"
    # Set after risk.escalate_for_small_groups widens this patient's small
    # cohort by dropping geography -- forces the actual geography field to
    # SUPPRESS in the transform so risk_after (computed from the same
    # widening decision) matches what the release really contains.
    suppress_geography: bool = False


def _earliest_reference_event(record_features_by_cluster: list[list]) -> object | None:
    best = None
    for feats in record_features_by_cluster:
        for f in feats:
            if f.role == FieldRole.DATE and f.scheme in _REFERENCE_EVENT_SCHEMES:
                try:
                    parsed = parse_flexible_date(f.original_text, getattr(f, 'date_convention', None))
                except DateParseError:
                    continue
                if best is None or parsed.value < best:
                    best = parsed.value
    return best


# A stated age ("Umar: 47", "Age/Sex: 46 Yrs/M") is weaker evidence than a DOB
# -- it is only true as at some unknown date -- so it is used only when no
# parseable DOB exists anywhere in the cluster. Bounded to a plausible human
# lifespan so a mis-parsed field cannot invent a band.
_MAX_PLAUSIBLE_AGE = 120
_AGE_DIGITS = re.compile(r"\b(\d{1,3})\b")


def _stated_age(record_features_by_cluster: list[list]) -> int | None:
    for feats in record_features_by_cluster:
        for f in feats:
            if f.role == FieldRole.DATE and f.scheme == "age":
                m = _AGE_DIGITS.search(f.original_text)
                if m is None:
                    continue
                age = int(m.group(1))
                if 0 <= age <= _MAX_PLAUSIBLE_AGE:
                    return age
    return None


def _dob_value(record_features_by_cluster: list[list]):
    for feats in record_features_by_cluster:
        for f in feats:
            if f.role == FieldRole.DATE and f.scheme == "dob":
                try:
                    return parse_flexible_date(f.original_text, getattr(f, 'date_convention', None)).value
                except DateParseError:
                    return "UNPARSEABLE"
    return None


def build_patient_contexts(
    clusters: dict[str, list[str]],
    record_features: dict[str, list],
    release_key: bytes,
    policy: PolicyConfig,
) -> dict[str, PatientContext]:
    """clusters: cluster_id -> member record_keys (every record, any link
    status). record_features: record_key -> that record's detected Features."""
    out: dict[str, PatientContext] = {}
    for cluster_id, members in clusters.items():
        canonical_key = "|".join(sorted(members))
        feats_lists = [record_features.get(m, []) for m in members]
        token = keys.patient_token(release_key, canonical_key)
        shift = keys.date_shift_days(release_key, canonical_key, policy.date_shift_min_days, policy.date_shift_max_days)

        ref_event = _earliest_reference_event(feats_lists)
        dob = _dob_value(feats_lists)
        age_band = None
        if dob not in (None, "UNPARSEABLE") and ref_event is not None:
            age_band = policy.age_band.band_for(age_years_at(dob, ref_event))
            reason = "DERIVED"
        else:
            # Fall back to an explicitly stated age. Without this a record
            # carrying an age column but no DOB (or no reference event to
            # measure a DOB against) got NO band at all, so the age field was
            # suppressed outright -- losing a truthful generalization the
            # scoring explicitly asks for, while an exact age elsewhere in the
            # same release stayed a re-identification key.
            stated = _stated_age(feats_lists)
            if stated is not None:
                age_band = policy.age_band.band_for(stated)
                reason = "DERIVED_FROM_STATED_AGE"
            elif dob is None:
                reason = "NO_DOB"
            elif dob == "UNPARSEABLE":
                reason = "DOB_UNPARSEABLE"
            else:
                reason = "NO_REFERENCE_EVENT"

        out[cluster_id] = PatientContext(
            cluster_id=cluster_id, canonical_key=canonical_key, patient_token=token,
            date_shift_days=shift, reference_event_date=ref_event, age_band=age_band, age_band_reason=reason,
        )
    return out
