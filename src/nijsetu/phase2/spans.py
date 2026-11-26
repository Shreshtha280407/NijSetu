"""Deterministic overlap resolution + right-to-left span replacement.

Shared by the CSV-cell, JSON-leaf and TXT-body transformers: each operates
on a text value plus the Features detected inside it, and must apply
non-overlapping replacements working from the end of the string backwards
(so earlier offsets stay valid as later ones are rewritten).
"""

from __future__ import annotations

from dataclasses import replace

from ..schema import Feature, FieldRole

_ROLE_PRIORITY: dict[FieldRole, int] = {
    FieldRole.PATIENT_IDENTIFIER: 6,
    FieldRole.CONTACT: 5,
    FieldRole.PATIENT_NAME: 4,
    FieldRole.RELATIVE_NAME: 4,
    FieldRole.PROVIDER_NAME: 3,
    FieldRole.FACILITY_NAME: 3,
    FieldRole.FINE_ADDRESS: 2,
    FieldRole.COARSE_GEOGRAPHY: 2,
    FieldRole.DATE: 1,
    FieldRole.CLINICAL_MEASUREMENT: 0,
    FieldRole.CLINICAL_HISTORY: 0,
    FieldRole.OTHER: -1,
}

_CONFIDENCE_RANK = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _is_better(a: Feature, b: Feature) -> bool:
    """True if a should win over b when their spans overlap."""
    ar, br = _ROLE_PRIORITY.get(a.role, -1), _ROLE_PRIORITY.get(b.role, -1)
    if ar != br:
        return ar > br
    ac, bc = _CONFIDENCE_RANK.get(a.confidence_tier, 0), _CONFIDENCE_RANK.get(b.confidence_tier, 0)
    if ac != bc:
        return ac > bc
    return (a.char_end - a.char_start) >= (b.char_end - b.char_start)


def resolve_overlaps(features: list[Feature]) -> list[Feature]:
    """Returns a sorted, non-overlapping list: overlapping spans are merged
    into one (widened to cover both), keeping the higher-priority feature's
    role/scheme/evidence but the union's start/end."""
    if not features:
        return []
    ordered = sorted(features, key=lambda f: (f.char_start, -f.char_end))
    merged: list[Feature] = [ordered[0]]
    for f in ordered[1:]:
        last = merged[-1]
        if f.char_start < last.char_end:
            winner = f if _is_better(f, last) else last
            new_start, new_end = min(last.char_start, f.char_start), max(last.char_end, f.char_end)
            merged[-1] = replace(winner, char_start=new_start, char_end=new_end)
        else:
            merged.append(f)
    return merged


def apply_span_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """replacements: (char_start, char_end, replacement_text), any order.
    Applied right to left so earlier offsets stay valid."""
    out = text
    for start, end, repl in sorted(replacements, key=lambda r: r[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out
