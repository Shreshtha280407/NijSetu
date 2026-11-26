"""Clinical false-positive guard: BP/HbA1c/dose/history markers must stay clinical,
never get reclassified as direct identifiers, and must not themselves trigger a
name-span false positive (a drug name is not a patient/relative name).
"""

from __future__ import annotations

import re

_BP_VALUE = re.compile(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b")

# A "NN/NN" shape is also the middle of a day/month/year date: "30/10/2026"
# offers "30/10". Treating that as a blood pressure is not merely untidy -- the
# bogus measurement is snapshotted from the INPUT, the real date is then
# shifted, the measurement no longer re-derives from the OUTPUT, the
# clinical-invariant check reports CLINICAL_MEASUREMENT_LOST_OR_CHANGED, and
# the only record in the document is held, which quarantines the whole file.
# Six discharge summaries were lost this way on the practice data.
#
# Two independent guards, both required:
#   * date context -- a further "/" plus digits on either side means this is a
#     date fragment, not a reading;
#   * physiological plausibility -- a real cuff reading has systolic 60-300,
#     diastolic 30-200, and systolic above diastolic. "30/10" fails on all
#     three; "126/86" passes.
_BP_SYSTOLIC_RANGE = (60, 300)
_BP_DIASTOLIC_RANGE = (30, 200)
_DATE_CONTEXT_AFTER = re.compile(r"\s*/\s*\d")
_DATE_CONTEXT_BEFORE = re.compile(r"\d\s*/\s*$")


def _is_plausible_blood_pressure(systolic: int, diastolic: int) -> bool:
    return (
        _BP_SYSTOLIC_RANGE[0] <= systolic <= _BP_SYSTOLIC_RANGE[1]
        and _BP_DIASTOLIC_RANGE[0] <= diastolic <= _BP_DIASTOLIC_RANGE[1]
        and systolic > diastolic
    )


def _is_date_fragment(text: str, start: int, end: int) -> bool:
    return bool(_DATE_CONTEXT_AFTER.match(text, end)) or bool(_DATE_CONTEXT_BEFORE.search(text[:start]))
_HBA1C = re.compile(r"\bHbA1c\b\s*[:\-]?\s*\d{1,2}(?:\.\d+)?\s*%", re.I)
_DOSE = re.compile(
    r"\b([A-Z][a-zA-Z]+)\s+(\d+(?:\.\d+)?)\s*(mg|ml|mcg|g|units?)\b"
    r"(?:\s+(once|twice|thrice|daily|bd|od|tds|qid))?",
    re.I,
)

_HISTORY_MARKER = re.compile(r"\b(?:K\s*/?\s*C\s*/?\s*O|KCO|H\s*/\s*O|HO)\b[:\-]?", re.I)
_CARE_OF_MARKER = re.compile(r"\bC\s*/\s*O\b[:\-]?", re.I)

_CLINICAL_COMPLAINT_WORDS = frozenset({
    "fever", "cough", "pain", "vomiting", "diarrhea", "diarrhoea", "weakness",
    "breathlessness", "giddiness", "headache", "swelling", "nausea", "fatigue",
    "diabetes", "hypertension", "asthma", "tuberculosis", "jaundice",
})


def find_clinical_measurements(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, kind) spans that must be retained as clinical values."""
    out: list[tuple[int, int, str]] = []
    for m in _BP_VALUE.finditer(text):
        if _is_date_fragment(text, m.start(), m.end()):
            continue
        if not _is_plausible_blood_pressure(int(m.group(1)), int(m.group(2))):
            continue
        out.append((m.start(), m.end(), "BLOOD_PRESSURE"))
    for m in _HBA1C.finditer(text):
        out.append((m.start(), m.end(), "HBA1C"))
    for m in _DOSE.finditer(text):
        out.append((m.start(2), m.end(3) if m.group(4) is None else m.end(4), "MEDICATION_DOSE"))
        out.append((m.start(1), m.end(1), "MEDICATION_NAME"))
    return out


def find_clinical_history_markers(text: str) -> list[tuple[int, int]]:
    """K/C/O, H/O-style markers: always clinical-history context, never a name trigger."""
    return [(m.start(), m.end()) for m in _HISTORY_MARKER.finditer(text)]


def classify_care_of(text: str, marker_end: int) -> str:
    """Disambiguates 'C/O' as address care-of (-> name follows) vs OPD complaint shorthand.

    Looks at the next word after the marker. A lowercase clinical-complaint
    word means "complains of" (clinical), not "care of" (a name).
    """
    tail = text[marker_end:marker_end + 40]
    m = re.search(r"[A-Za-z][A-Za-z']*", tail)
    if not m:
        return "AMBIGUOUS"
    word = m.group(0)
    if word.lower() in _CLINICAL_COMPLAINT_WORDS:
        return "CLINICAL_COMPLAINT"
    if word[0].isupper():
        return "NAME_FOLLOWS"
    return "AMBIGUOUS"


def find_care_of_markers(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _CARE_OF_MARKER.finditer(text):
        out.append((m.start(), m.end(), classify_care_of(text, m.end())))
    return out
