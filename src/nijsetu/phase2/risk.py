"""Defensive patient-level risk analysis: quasi-identifier coverage,
singleton/small-group counts, rare-combination flags, and a small explainable
widening ladder -- computed over DISTINCT PATIENTS (one signature per
cluster_id), never per-record, so repeated encounters never inflate group
size and never create false reassurance either.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Optional

from ..schema import FieldRole
from .dates import DateParseError, parse_flexible_date
from .patient_context import PatientContext
from .policy import PolicyConfig


@dataclass(frozen=True)
class PatientSignature:
    cluster_id: str
    age_band: Optional[str]
    geography: Optional[str]
    visit_period: Optional[str]  # "YYYY-MM" / "YYYY" / None
    diagnosis_terms: tuple[str, ...]
    facility: Optional[str]


def _group_key(sig: PatientSignature) -> tuple:
    return (sig.age_band, sig.geography, sig.visit_period)


def build_signatures(
    clusters: dict[str, list[str]],
    record_features: dict[str, list],
    patient_contexts: dict[str, PatientContext],
) -> dict[str, PatientSignature]:
    out: dict[str, PatientSignature] = {}
    for cluster_id, members in clusters.items():
        geography = None
        diagnosis: set[str] = set()
        facility = None
        for m in members:
            for f in record_features.get(m, []):
                if f.role == FieldRole.COARSE_GEOGRAPHY and geography is None and f.original_text.strip():
                    geography = f.original_text.strip().lower()
                elif f.role == FieldRole.CLINICAL_HISTORY and f.original_text.strip():
                    diagnosis.add(f.original_text.strip().lower())
                elif f.role == FieldRole.FACILITY_NAME and facility is None and f.original_text.strip():
                    facility = f.original_text.strip().lower()
        ctx = patient_contexts[cluster_id]
        visit_period = ctx.reference_event_date.strftime("%Y-%m") if ctx.reference_event_date else None
        out[cluster_id] = PatientSignature(
            cluster_id=cluster_id, age_band=ctx.age_band, geography=geography,
            visit_period=visit_period, diagnosis_terms=tuple(sorted(diagnosis)), facility=facility,
        )
    return out


def build_released_signatures(
    clusters: dict[str, list[str]],
    record_features: dict[str, list],
    patient_contexts: dict[str, PatientContext],
) -> dict[str, PatientSignature]:
    """Use only reparsed, surviving output values for post-release risk.

    Input reference dates precede the patient-specific shift, and may even
    belong to held records. Neither those dates nor an unexported age band
    may describe the released cohort. Do not shift these dates again.
    """
    contexts = {}
    event_schemes = {"admission_date", "discharge_date", "encounter_date", "specimen_date", "report_date"}
    for cid, members in clusters.items():
        ctx = patient_contexts[cid]
        dates = []
        age_band = None
        for member in members:
            for feature in record_features.get(member, []):
                if feature.role != FieldRole.DATE:
                    continue
                if feature.scheme == "dob":
                    if ctx.age_band is not None and feature.original_text.strip() == ctx.age_band:
                        age_band = ctx.age_band
                elif feature.scheme in event_schemes:
                    try:
                        dates.append(parse_flexible_date(feature.original_text).value)
                    except DateParseError:
                        continue
        contexts[cid] = replace(ctx, reference_event_date=min(dates) if dates else None, age_band=age_band)
    return build_signatures(clusters, record_features, contexts)


@dataclass
class RiskReport:
    total_patients: int
    qi_coverage: dict[str, float]
    group_sizes: dict[str, int]  # str(group_key) -> count, JSON-safe
    singleton_count: int
    singleton_rate: float
    small_group_count: int
    small_group_rate: float
    k_threshold: int
    distinctive_combo_count: int


def analyze_risk(signatures: dict[str, PatientSignature], k_threshold: int) -> RiskReport:
    total = len(signatures)
    counts = Counter(_group_key(s) for s in signatures.values())
    singleton = sum(1 for s in signatures.values() if counts[_group_key(s)] == 1)
    small = sum(1 for s in signatures.values() if counts[_group_key(s)] < k_threshold)

    def coverage(pred) -> float:
        return (sum(1 for s in signatures.values() if pred(s)) / total) if total else 0.0

    distinctive = 0
    diag_totals = Counter(term for s in signatures.values() for term in s.diagnosis_terms)
    for s in signatures.values():
        if counts[_group_key(s)] == 1 and any(diag_totals[t] == 1 for t in s.diagnosis_terms):
            distinctive += 1

    return RiskReport(
        total_patients=total,
        qi_coverage={
            "age_band": coverage(lambda s: s.age_band is not None),
            "geography": coverage(lambda s: s.geography is not None),
            "visit_period": coverage(lambda s: s.visit_period is not None),
            "diagnosis": coverage(lambda s: bool(s.diagnosis_terms)),
        },
        group_sizes={str(k): v for k, v in counts.items()},
        singleton_count=singleton,
        singleton_rate=(singleton / total) if total else 0.0,
        small_group_count=small,
        small_group_rate=(small / total) if total else 0.0,
        k_threshold=k_threshold,
        distinctive_combo_count=distinctive,
    )


def escalate_for_small_groups(
    signatures: dict[str, PatientSignature], policy: PolicyConfig,
) -> tuple[dict[str, PatientSignature], set[str]]:
    """Least-disruptive widening: drop geography for any patient whose group
    is below k_threshold -- the one widening step this module actually wires
    into the real release (sanitize.py forces the matching patient's
    geography field to SUPPRESS in the transform for exactly these
    cluster_ids, via PatientContext.suppress_geography), so risk_after never
    claims a mitigation the output does not contain.

    Visit-period date generalization is deliberately NOT modelled here: the
    release's admission/discharge interval and order must survive intact
    (the required worked case's 5-day stay), so coarsening or dropping a
    patient's visit date is never actually applied to the output -- and a
    risk report must not describe a date as suppressed/generalized when the
    detailed date remains. The ladder therefore retains the full shifted
    timeline for every patient and reports the genuine residual risk,
    rather than a date-widening effect that was never enacted.

    Returns (possibly-widened signatures, cluster_ids to hold). Holding is
    only ever populated when policy.hold_distinctive_singletons is set, and
    even then only for a patient whose final group is still a singleton AND
    carries a diagnosis term that is release-wide rare (count == 1) --
    never for group size alone, so a merely-small synthetic cohort is never
    blanket-suppressed."""
    sigs = dict(signatures)
    counts = Counter(_group_key(s) for s in sigs.values())
    small_ids = [cid for cid, s in sigs.items() if counts[_group_key(s)] < policy.k_threshold]
    for cid in small_ids:
        s = sigs[cid]
        if s.geography is not None:
            sigs[cid] = replace(s, geography=None)

    held: set[str] = set()
    if policy.hold_distinctive_singletons:
        final_counts = Counter(_group_key(s) for s in sigs.values())
        diag_totals = Counter(term for s in sigs.values() for term in s.diagnosis_terms)
        for cid, s in sigs.items():
            if final_counts[_group_key(s)] == 1 and any(diag_totals[t] == 1 for t in s.diagnosis_terms):
                held.add(cid)
    return sigs, held
