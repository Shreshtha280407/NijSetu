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
    age_band_reason: str  # "DERIVED" | "NO_DOB" | "NO_REFERENCE_EVENT" | "DOB_UNPARSEABLE"
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
                    parsed = parse_flexible_date(f.original_text)
                except DateParseError:
                    continue
                if best is None or parsed.value < best:
                    best = parsed.value
    return best


def _dob_value(record_features_by_cluster: list[list]):
    for feats in record_features_by_cluster:
        for f in feats:
            if f.role == FieldRole.DATE and f.scheme == "dob":
                try:
                    return parse_flexible_date(f.original_text).value
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
        if dob is None:
            reason = "NO_DOB"
        elif dob == "UNPARSEABLE":
            reason = "DOB_UNPARSEABLE"
        elif ref_event is None:
            reason = "NO_REFERENCE_EVENT"
        else:
            age = age_years_at(dob, ref_event)
            age_band = policy.age_band.band_for(age)
            reason = "DERIVED"

        out[cluster_id] = PatientContext(
            cluster_id=cluster_id, canonical_key=canonical_key, patient_token=token,
            date_shift_days=shift, reference_event_date=ref_event, age_band=age_band, age_band_reason=reason,
        )
    return out
