"""Clinical utility invariants: capture a typed assertion snapshot from the
original record, then independently re-derive the same assertions from the
REPARSED staged output (using the same detection pipeline, not the in-memory
transform's own bookkeeping) and compare. A mismatch blocks publication of
the affected record -- this is what catches a real transform/serialization
bug, not merely a logic trace through the code that wrote the output.

Required worked case this directly covers: BP 140/90, HbA1c 8.2%, Ramipril
5 mg twice daily all textually unchanged; a 12 Oct -> 17 Oct (5-day) stay
still a 5-day stay after both dates shift by the same patient offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..schema import Feature, FieldRole
from .dates import DateParseError, parse_flexible_date

_INTERVAL_SCHEMES = ("admission_date", "discharge_date")


@dataclass(frozen=True)
class Assertions:
    measurements: frozenset  # frozenset[tuple[str, str]] -- (kind, exact text)
    history: frozenset  # frozenset[str]
    dates: tuple  # tuple[tuple[str, date], ...] -- (scheme, parsed date), first occurrence per scheme


def extract_assertions(features: list[Feature]) -> Assertions:
    measurements = set()
    history = set()
    dates: dict[str, date] = {}
    for f in features:
        if f.role == FieldRole.CLINICAL_MEASUREMENT:
            # `Feature` (free-text detection) carries `.evidence`; a plain
            # ingest `Field` (e.g. FHIR's structural code/valueQuantity/
            # dosageInstruction extraction) does not -- both are valid
            # inputs here (see fhir_rewrite.rewrite_resource's reparse).
            evidence = getattr(f, "evidence", None)
            kind = evidence[0].rule_id if evidence else "UNKNOWN"
            measurements.add((kind, f.original_text))
        elif f.role == FieldRole.CLINICAL_HISTORY:
            history.add(f.original_text)
        elif f.role == FieldRole.DATE and f.scheme in _INTERVAL_SCHEMES and f.scheme not in dates:
            try:
                dates[f.scheme] = parse_flexible_date(f.original_text, getattr(f, 'date_convention', None)).value
            except DateParseError:
                continue
    return Assertions(frozenset(measurements), frozenset(history), tuple(sorted(dates.items())))


def compare_assertions(original: Assertions, staged: Assertions, offset_days: int) -> list[str]:
    """offset_days: the shift applied to the owning patient's dates. Only
    interval/order are checked here (not an exact shifted-date-text equality,
    since staged.dates come from re-parsing the staged record itself, which
    already reflects whatever the transform actually wrote)."""
    problems: list[str] = []
    if original.measurements - staged.measurements:
        problems.append("CLINICAL_MEASUREMENT_LOST_OR_CHANGED")
    if original.history - staged.history:
        problems.append("CLINICAL_HISTORY_LOST_OR_CHANGED")

    orig_dates = dict(original.dates)
    staged_dates = dict(staged.dates)
    if "admission_date" in orig_dates and "discharge_date" in orig_dates:
        orig_duration = (orig_dates["discharge_date"] - orig_dates["admission_date"]).days
        if "admission_date" not in staged_dates or "discharge_date" not in staged_dates:
            problems.append("CLINICAL_INTERVAL_DATES_MISSING_AFTER_TRANSFORM")
        else:
            staged_duration = (staged_dates["discharge_date"] - staged_dates["admission_date"]).days
            if staged_duration != orig_duration:
                problems.append("CLINICAL_INTERVAL_DURATION_CHANGED")
            expected_admission = orig_dates["admission_date"].toordinal() + offset_days
            if staged_dates["admission_date"].toordinal() != expected_admission:
                problems.append("CLINICAL_DATE_SHIFT_INCONSISTENT")
    return problems
