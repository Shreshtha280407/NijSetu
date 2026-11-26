"""Aadhaar-like / ABHA / UHID / phone / email / PIN detectors.

Format and checksum evidence SUPPORTS classification; it never proves
ownership. A checksum-invalid value in a strongly identifying field (12
grouped digits, a labelled Aadhaar field, ...) still gets flagged -- the
checksum only raises or lowers confidence_tier, it never gates detection.
"""

from __future__ import annotations

import re

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_checksum_valid(digits: str) -> bool:
    """Aadhaar uses the Verhoeff algorithm as its check digit. Evidence, not proof."""
    if not digits.isdigit():
        return False
    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


_AADHAAR_PLAIN = re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b")
_AADHAAR_MASKED = re.compile(r"\b[xX*]{4}[\s-]?[xX*]{4}[\s-]?(\d{4})\b")
_ABHA_NUMBER = re.compile(r"\b(\d{2})-?(\d{4})-?(\d{4})-?(\d{4})\b")
_ABHA_ADDRESS = re.compile(r"\b[\w.+-]+@(abdm|sbx|ndhm|abha)\b", re.I)
_PHONE_IN = re.compile(r"(?:\+?91[\-\s]?|0)?\b[6-9]\d{9}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PIN = re.compile(r"\b[1-9]\d{5}\b")


def find_aadhaar_like(text: str) -> list[tuple[int, int, str, bool, bool]]:
    """Returns (start, end, kind, is_masked, checksum_valid_or_na)."""
    out = []
    for m in _AADHAAR_PLAIN.finditer(text):
        digits = m.group(1) + m.group(2) + m.group(3)
        out.append((m.start(), m.end(), "AADHAAR_PLAIN", False, verhoeff_checksum_valid(digits)))
    for m in _AADHAAR_MASKED.finditer(text):
        out.append((m.start(), m.end(), "AADHAAR_MASKED", True, False))
    return out


def find_abha_number(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _ABHA_NUMBER.finditer(text)]


def find_abha_address(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _ABHA_ADDRESS.finditer(text)]


def find_phone(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PHONE_IN.finditer(text)]


def find_email(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _EMAIL.finditer(text):
        if _ABHA_ADDRESS.fullmatch(m.group(0)):
            continue  # already classified as an ABHA address, not a generic email
        out.append((m.start(), m.end()))
    return out


def find_pin_code(text: str) -> list[tuple[int, int]]:
    out = []
    for m in re.finditer(r"\bpin(?:code)?\b\s*[:\-]?\s*([1-9]\d{5})\b", text, re.I):
        out.append((m.start(1), m.end(1)))
    return out


_UHID_LABELLED = re.compile(r"\b(?:UHID|MRN)\b\s*[:\-#]?\s*([A-Za-z0-9/-]{3,20})\b")


def find_labelled_uhid(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _UHID_LABELLED.finditer(text):
        out.append((m.start(1), m.end(1)))
    return out


# Label-anchored free-text dates: a bare date never carries its own kind, so
# -- same principle as the labelled UHID above -- only a clinical-timeline
# LABEL (admission/discharge/encounter/specimen/report/DOB) lets a date
# mentioned in a note be classified and consistently shifted, exactly like a
# structured "Admission Date" CSV/JSON column (see ingest.header_aliases).
_DATE_VALUE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{4}"
    r"|\d{4}/\d{1,2}/\d{1,2}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"
)
_DATE_LABELS: tuple[tuple[str, re.Pattern], ...] = (
    ("admission_date", re.compile(r"\badmission\s*date\b\s*[:\-]?\s*", re.I)),
    ("discharge_date", re.compile(r"\bdischarge\s*date\b\s*[:\-]?\s*", re.I)),
    ("encounter_date", re.compile(r"\b(?:encounter|visit|consultation)\s*date\b\s*[:\-]?\s*", re.I)),
    ("specimen_date", re.compile(r"\b(?:specimen|collection|sample)\s*date\b\s*[:\-]?\s*", re.I)),
    ("report_date", re.compile(r"\breport\s*date\b\s*[:\-]?\s*", re.I)),
    ("dob", re.compile(r"\b(?:d\.?o\.?b\.?|date of birth)\b\s*[:\-]?\s*", re.I)),
)


def find_labelled_dates(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, date_kind) for the VALUE span only (never the label)."""
    out = []
    for kind, label_re in _DATE_LABELS:
        for m in label_re.finditer(text):
            val = _DATE_VALUE.match(text, m.end())
            if val:
                out.append((val.start(), val.end(), kind))
    return out
