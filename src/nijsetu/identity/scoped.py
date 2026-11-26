"""Scoped identifier resolution: exact (issuer, scheme, value) lookups only.

The same UHID value from two different hospitals (different issuer scope)
must never be treated as the same identifier -- scope is part of the key.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..ingest.header_aliases import HEADER_ALIASES
from ..schema import ScopedIdentifier

_NATIONAL_SCHEMES = frozenset({"aadhaar", "abha_number", "abha_address"})
# Only a scheme name drawn from this fixed, bounded vocabulary is safe to
# echo in a public reason code. A FHIR identifier.system URL (or anything
# else read from file content) is deliberately excluded -- see
# ingest.fhir_adapter, which falls back to the generic "fhir_identifier"
# scheme precisely when there is no fixed system URI to rely on.
_KNOWN_SAFE_SCHEMES = frozenset(HEADER_ALIASES) | {"fhir_identifier"}


@dataclass(frozen=True)
class ScopeKey:
    issuer: str
    scheme: str
    value: str


def _public_scope_label(key: ScopeKey) -> str:
    """A fixed, bounded public label for an exact scoped-identifier match.

    Never the issuer string itself: an issuer can be a raw file path (the
    per-file UNKNOWN_SOURCE fallback), a value read straight from file
    content (a FACILITY_NAME field, a FHIR identifier.system URL), or an
    operator-chosen --source-system string -- none of that is safe to echo
    into reason_codes, which flow into the SAFE report's
    reason_code_tally. The real issuer/scheme is still fully preserved,
    per feature, in the PRIVATE artifact only (see plan.builder).
    """
    scheme_label = key.scheme if key.scheme in _KNOWN_SAFE_SCHEMES else "OTHER"
    if key.scheme in _NATIONAL_SCHEMES:
        kind = "NATIONAL_ID"
    elif key.issuer.startswith("UNKNOWN_SOURCE"):
        kind = "UNKNOWN_SOURCE"
    else:
        kind = "CONFIGURED_SOURCE"
    return f"{kind}:{scheme_label}"


def build_scoped_index(
    record_scoped_ids: dict[str, list[ScopedIdentifier]],
) -> dict[ScopeKey, list[str]]:
    """record_key -> its ScopedIdentifiers; returns ScopeKey -> [record_keys]."""
    index: dict[ScopeKey, list[str]] = defaultdict(list)
    for record_key, idents in record_scoped_ids.items():
        for ident in idents:
            key = ScopeKey(ident.issuer, ident.scheme, ident.value)
            index[key].append(record_key)
    return index


def exact_match_edges(index: dict[ScopeKey, list[str]]) -> list[tuple[str, str, str]]:
    """Returns (record_a, record_b, scope_label) edges for every exact scoped match."""
    edges: list[tuple[str, str, str]] = []
    for key, records in index.items():
        unique = sorted(set(records))
        if len(unique) < 2:
            continue
        label = _public_scope_label(key)
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                edges.append((unique[i], unique[j], label))
    return edges
