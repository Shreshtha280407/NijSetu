"""Clinical false-positive guard: BP/HbA1c/dose/history markers must stay clinical,
never get reclassified as direct identifiers, and must not themselves trigger a
name-span false positive (a drug name is not a patient/relative name).
"""

from __future__ import annotations

import re

_BP_VALUE = re.compile(r"\b\d{2,3}\s*/\s*\d{2,3}\b")
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
