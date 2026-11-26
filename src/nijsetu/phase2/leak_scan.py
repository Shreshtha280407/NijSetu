"""Independent residual leak scan over the actual serialized staged files and
publishable paths -- run after transform/staging, before publication, as a
check that does not trust the transform code path that produced the output.

Two independent mechanisms:
  1. Literal scan: every known private literal (original PATIENT_NAME /
     RELATIVE_NAME / PATIENT_IDENTIFIER / CONTACT / FINE_ADDRESS span text,
     plus original source file/directory basenames) must never reappear in
     any staged file's content OR path.
  2. Generic format recognizers (Aadhaar / ABHA / phone / email / PIN) that
     have zero legitimate retained occurrence under any current policy --
     unlike a labelled UHID (deliberately tokenized in place, so a label
     text followed by a safe token is expected and excluded here), these
     patterns are never written by the transform under any policy, so a
     match is unambiguous.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..detect.identifiers import find_aadhaar_like, find_abha_address, find_abha_number, find_email, find_pan, find_phone, find_pin_code

_RELEASED_FILE_NO = re.compile(r"released/file_(\d+)\.")


def _read_text_safely(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _clean(literals: list[str]) -> list[str]:
    return sorted({lit for lit in literals if lit and len(lit) >= 3})


# A literal is searched as a plain substring, which is right for a full name or
# an MRN but wrong for a SHORT, purely-numeric one. A three-digit house number
# ("H.No. 364" -> the literal "364") occurs by coincidence inside unrelated
# digits all the time: inside a phone number ("060062-72163"), inside a PIN, and
# -- worst -- inside our OWN minted base32 tokens ("...te346nnoj..."), whose
# alphabet includes decimal digits. Each such coincidence was reported as a
# residual CONTENT_LEAK and quarantined the whole file. One of those files held
# 30 required-link records.
#
# Two guards, applied only where they are needed:
#   * a short numeric literal must match on a DIGIT boundary, so "364" matches
#     "H.No. 364" but not "72163" or "...te346nnoj...";
#   * any literal occurrence lying inside a value this release itself minted is
#     never a residual leak -- the source never contained that string, HMAC
#     output did. `scan_generic_residual_patterns` already had this exclusion;
#     the literal scan did not.
_SHORT_NUMERIC_MAX_LEN = 6


def _needs_digit_boundary(literal: str) -> bool:
    return literal.isdigit() and len(literal) <= _SHORT_NUMERIC_MAX_LEN


def _literal_occurrences(text: str, literal: str) -> list[int]:
    """Start offsets of every occurrence of `literal` that counts as a hit."""
    boundary = _needs_digit_boundary(literal)
    out: list[int] = []
    start = text.find(literal)
    while start != -1:
        end = start + len(literal)
        if not boundary or (
            (start == 0 or not text[start - 1].isdigit())
            and (end == len(text) or not text[end].isdigit())
        ):
            out.append(start)
        start = text.find(literal, start + 1)
    return out


def _minted_spans(text: str, minted_tokens: frozenset[str]) -> list[tuple[int, int]]:
    """Character ranges in `text` occupied by values this release minted."""
    spans: list[tuple[int, int]] = []
    for token in minted_tokens:
        if len(token) < 8:  # too short to be one of our tokens; never worth scanning
            continue
        start = text.find(token)
        while start != -1:
            spans.append((start, start + len(token)))
            start = text.find(token, start + len(token))
    return spans


def _literal_leaks(text: str, literal: str, minted_spans: list[tuple[int, int]]) -> bool:
    """True when `literal` occurs somewhere that is a genuine residual leak:
    on a real boundary (for short numeric literals) and outside every value we
    minted ourselves."""
    for start in _literal_occurrences(text, literal):
        end = start + len(literal)
        if not any(span_start <= start and end <= span_end for span_start, span_end in minted_spans):
            return True
    return False


def scan_literals(
    staging_root: Path,
    file_literals: dict[int, list[str]],
    minted_tokens: frozenset[str] = frozenset(),
) -> list[str]:
    """Checks each staged output file ONLY against the PII literals actually
    detected inside that SAME input file -- not the whole corpus's literals.
    A global (corpus-wide) check would flag a common first name retained as
    a doctor's name in file B purely because an unrelated patient in file A
    happened to share it ("a doctor called Ramesh does not turn a removed
    relative into a leak", per the organizer's own SCORING.md) -- false
    positives of exactly that shape caused whole, genuinely-clean files to
    be quarantined once real multi-hospital data was tested, which is a
    correctness bug, not a stricter check. A path that cannot be attributed
    to one input file falls back to the union of every file's literals
    (fail-safe default, unreachable in practice: every scan target at this
    point in the pipeline lives under released/file_N.*)."""
    findings: list[str] = []
    all_literals = _clean([lit for lits in file_literals.values() for lit in lits])
    if not all_literals:
        return findings
    cleaned_by_file = {no: _clean(lits) for no, lits in file_literals.items()}
    for path in sorted(staging_root.rglob("*")):
        rel = str(path.relative_to(staging_root))
        m = _RELEASED_FILE_NO.search(rel)
        literals = cleaned_by_file.get(int(m.group(1)), []) if m else all_literals
        for lit in literals:
            if _literal_leaks(rel, lit, []):
                findings.append(f"FILENAME_LEAK:{rel}:{_redacted_hint(lit)}")
        if path.is_file():
            text = _read_text_safely(path)
            if text is None:
                continue
            spans = _minted_spans(text, minted_tokens)
            for lit in literals:
                if _literal_leaks(text, lit, spans):
                    findings.append(f"CONTENT_LEAK:{rel}:{_redacted_hint(lit)}")
    return findings


def _redacted_hint(literal: str) -> str:
    """A findable description of WHICH literal leaked, carrying no PII itself.

    A residual-leak finding quarantines a whole file, so whoever reads the
    report has to be able to tell which detector produced the literal and fix
    the transform -- but the finding travels into report.json, where the
    literal itself must never appear. Length plus a shape sketch is enough to
    identify it against the private artifact and tells an attacker nothing the
    file's existence did not.
    """
    shape = "".join(
        "9" if c.isdigit() else "A" if c.isalpha() and c.isascii() else "x" if c.isalpha() else c
        for c in literal[:24]
    )
    return f"len{len(literal)}:{shape}"


def _is_coincidental_digit_run(start: int, end: int, text: str, minted_tokens: frozenset[str]) -> bool:
    """A digit-run pattern (Aadhaar/ABHA/phone) can coincidentally match
    inside a value NijSetu itself just minted -- a resource UUID's hex
    groups, or (far less likely) a base32 token -- purely because hex/base32
    alphabets include decimal digits. That is never a residual leak (the
    source data never contained this string; HMAC output did), so it must
    not consume the whole-release-block budget. Only the exact matched
    substring is checked against each minted value, never the other way
    around, so a genuine Aadhaar number sitting right next to one of our
    tokens in the same file still matches normally."""
    matched = text[start:end]
    return any(matched in token for token in minted_tokens)


def _collect_structural_ids(node: Any, out: set[str]) -> None:
    """A FHIR resource's own `id`, `fullUrl` and `reference` fields are
    ALWAYS exactly `keys.resource_uuid(...)` output in a NijSetu-sanitized
    Bundle -- never raw source data -- so they are safe to exclude from the
    generic digit-pattern scan on sight, with no private key material
    needed (unlike `minted_tokens`, this works for validate_release's
    standalone, key-less scan of an already-published release too)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("id", "fullUrl") and isinstance(value, str):
                out.add(value)
            if key == "reference" and isinstance(value, str) and value.startswith("urn:uuid:"):
                out.add(value[len("urn:uuid:"):])
            _collect_structural_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_structural_ids(item, out)


def _file_safe_tokens(path: Path, text: str, minted_tokens: frozenset[str]) -> frozenset[str]:
    if path.suffix.lower() != ".json":
        return minted_tokens
    try:
        data = json.loads(text)
    except ValueError:
        return minted_tokens
    structural: set[str] = set()
    _collect_structural_ids(data, structural)
    return frozenset(minted_tokens | structural) if structural else minted_tokens


def scan_generic_residual_patterns(staging_root: Path, minted_tokens: frozenset[str] = frozenset()) -> list[str]:
    findings: list[str] = []
    for path in sorted(staging_root.rglob("*")):
        if not path.is_file():
            continue
        text = _read_text_safely(path)
        if not text:
            continue
        rel = str(path.relative_to(staging_root))
        safe = _file_safe_tokens(path, text, minted_tokens)
        if any(not _is_coincidental_digit_run(s, e, text, safe) for s, e, *_ in find_aadhaar_like(text)):
            findings.append(f"RESIDUAL_AADHAAR_PATTERN:{rel}")
        if any(not _is_coincidental_digit_run(s, e, text, safe) for s, e in find_abha_number(text)):
            findings.append(f"RESIDUAL_ABHA_NUMBER_PATTERN:{rel}")
        if find_abha_address(text):
            findings.append(f"RESIDUAL_ABHA_ADDRESS_PATTERN:{rel}")
        if any(not _is_coincidental_digit_run(s, e, text, safe) for s, e in find_phone(text)):
            findings.append(f"RESIDUAL_PHONE_PATTERN:{rel}")
        if find_email(text):
            findings.append(f"RESIDUAL_EMAIL_PATTERN:{rel}")
        if any(m not in safe for s, e in find_pan(text) for m in [text[s:e]]):
            findings.append(f"RESIDUAL_PAN_PATTERN:{rel}")
        if find_pin_code(text):
            findings.append(f"RESIDUAL_PIN_PATTERN:{rel}")
    return findings
