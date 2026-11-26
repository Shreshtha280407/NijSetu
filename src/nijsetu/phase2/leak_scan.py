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

from pathlib import Path

from ..detect.identifiers import find_aadhaar_like, find_abha_address, find_abha_number, find_email, find_phone, find_pin_code


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


def scan_literals(staging_root: Path, literals: list[str]) -> list[str]:
    findings: list[str] = []
    literals = sorted({lit for lit in literals if lit and len(lit) >= 3})
    if not literals:
        return findings
    for path in sorted(staging_root.rglob("*")):
        rel = str(path.relative_to(staging_root))
        for lit in literals:
            if lit in rel:
                findings.append(f"FILENAME_LEAK:{rel}")
        if path.is_file():
            text = _read_text_safely(path)
            if text is None:
                continue
            for lit in literals:
                if lit in text:
                    findings.append(f"CONTENT_LEAK:{rel}")
    return findings


def scan_generic_residual_patterns(staging_root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(staging_root.rglob("*")):
        if not path.is_file():
            continue
        text = _read_text_safely(path)
        if not text:
            continue
        rel = str(path.relative_to(staging_root))
        if find_aadhaar_like(text):
            findings.append(f"RESIDUAL_AADHAAR_PATTERN:{rel}")
        if find_abha_number(text):
            findings.append(f"RESIDUAL_ABHA_NUMBER_PATTERN:{rel}")
        if find_abha_address(text):
            findings.append(f"RESIDUAL_ABHA_ADDRESS_PATTERN:{rel}")
        if find_phone(text):
            findings.append(f"RESIDUAL_PHONE_PATTERN:{rel}")
        if find_email(text):
            findings.append(f"RESIDUAL_EMAIL_PATTERN:{rel}")
        if find_pin_code(text):
            findings.append(f"RESIDUAL_PIN_PATTERN:{rel}")
    return findings
