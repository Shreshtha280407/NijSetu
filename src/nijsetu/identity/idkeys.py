"""Issuer-independent identifier keys: canonical form, series, distinctiveness.

Kept in its own module because both the candidate-feature builder and the pair
scorer need it, and a shared helper in the package __init__ would make the
scorer import the package that imports the scorer.
"""

from __future__ import annotations

import re

# A hospital-local identifier normally carries its issuing facility in its own
# leading alphabetic prefix -- "JJH-0077058", "SNJ/26/064423", "DHH2026-084999".
#
# That prefix is NOT used as the identifier's issuer namespace. It is tempting
# (it would let the same real UHID in two different files form an exact edge),
# but it is unsound and was measured to be so: a prefix is frequently a scheme
# word rather than a facility code ("LOCAL-001", "UHID-010610", "MRN-44"), so
# two genuinely unrelated hospitals that both number patients "LOCAL-001"
# collapse into one namespace and their different patients exact-match. It also
# silently overrides a deliberately configured --source-system declaration,
# which is the one piece of authoritative operator evidence in the system.
#
# The prefix is used instead for what it can actually support:
#
#   * identifier SERIES membership (`issuer_prefix_of`). Two values sharing a
#     prefix are drawn from one facility's numbering series, so if they differ
#     they denote two different patients -- the contradiction that separates
#     two homonymous patients who each hold a JJH number. Two values with
#     DIFFERENT prefixes say nothing: one person legitimately holds a local id
#     at each hospital they attend.
#   * identifier VALUE agreement (see identity.scoring). A rare identifier
#     value appearing in two records is strong corroborating evidence even
#     when the namespace is unproven -- which is how a per-file-scoped bulk
#     lab export reaches its patient's registration record without having to
#     pretend the file and the hospital are the same issuer.
_ID_PREFIX = re.compile(r"^\s*([A-Za-z]{2,12})")
_MIN_ISSUER_PREFIX_LEN = 2

# Characters that merely format an identifier for humans. "JJH-0077058",
# "JJH/0077058" and "jjh 0077058" are one value written three ways; comparing
# them raw makes a genuine agreement invisible.
_ID_NOISE = re.compile(r"[^0-9A-Za-z]+")


def issuer_prefix_of(value: str) -> str | None:
    """The identifier series (facility numbering scheme) a value belongs to."""
    match = _ID_PREFIX.match(value)
    if match is None:
        return None
    prefix = match.group(1).upper()
    return prefix if len(prefix) >= _MIN_ISSUER_PREFIX_LEN else None


def canonical_identifier(value: str) -> str:
    """Comparison form of an identifier value: case- and separator-agnostic."""
    return _ID_NOISE.sub("", value).upper()


def informative_series(scheme: str, prefix: str) -> str | None:
    """The facility numbering series a prefix identifies, or None when the
    prefix carries no facility information.

    A prefix that merely restates the scheme it belongs to ("UHID-010610",
    "MRN/4471", "ABHA 12...") names no facility: every hospital that formats
    its ids that way produces the same prefix, so reading two such values as
    one series would make unrelated patients contradict each other. Derived
    from the scheme name itself rather than from a list of known hospital
    codes, so it generalises to facilities and formats not seen here.
    """
    if not prefix:
        return None
    normalized_scheme = _ID_NOISE.sub("", scheme).upper()
    if not normalized_scheme:
        return prefix
    if prefix in normalized_scheme or normalized_scheme in prefix:
        return None
    return prefix
