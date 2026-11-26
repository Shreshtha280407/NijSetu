"""Candidate generation: narrow blocking indexes instead of all-pairs comparison.

Several independent blocks each index records into small buckets; candidate
pairs are the union, deduplicated. Oversized/common buckets are capped
rather than fully expanded, with the truncation recorded so recall loss is
visible, not silent.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from ..schema import PatientCandidateFeatures


def _bucket_keys_name_year(f: PatientCandidateFeatures) -> list[str]:
    if not f.normalized_name_tokens or f.birth_year is None:
        return []
    return [f"NY:{f.normalized_name_tokens[0]}:{f.birth_year}"]


def _bucket_keys_name_locality(f: PatientCandidateFeatures) -> list[str]:
    if not f.normalized_name_tokens or not f.address_tokens:
        return []
    return [f"NL:{f.normalized_name_tokens[0]}:{f.address_tokens[0]}"]


def _bucket_keys_skeleton_clue(f: PatientCandidateFeatures) -> list[str]:
    if not f.skeletons:
        return []
    clue = f.phone_digits[0] if f.phone_digits else (f.address_tokens[0] if f.address_tokens else None)
    if clue is None:
        return []
    return [f"SK:{f.skeletons[-1]}:{clue}"]


def _bucket_keys_skeleton_only(f: PatientCandidateFeatures) -> list[str]:
    """Per-token skeleton index -- the primary route for the sparse-evidence case
    (a bare cue-detected name with no DOB/phone/address at all), since it needs
    no corroborating field to generate a candidate. Scoring, not blocking,
    decides whether the eventual match is accepted.
    """
    return [f"SKO:{sk}" for sk in f.skeletons if len(sk) >= 2]


_BLOCKERS = (
    _bucket_keys_name_year,
    _bucket_keys_name_locality,
    _bucket_keys_skeleton_clue,
    _bucket_keys_skeleton_only,
)


def generate_candidate_pairs(
    features: list[PatientCandidateFeatures],
    max_bucket: int,
) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Returns (deduplicated pairs, {bucket_key: original_size} for truncated buckets)."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for f in features:
        for blocker in _BLOCKERS:
            for key in blocker(f):
                buckets[key].append(f.record_key)

    pairs: set[tuple[str, str]] = set()
    truncated: dict[str, int] = {}
    for key, members in buckets.items():
        unique_members = sorted(set(members))
        if len(unique_members) > max_bucket:
            truncated[key] = len(unique_members)
            unique_members = unique_members[:max_bucket]
        for a, b in combinations(unique_members, 2):
            pairs.add((a, b) if a < b else (b, a))
    return sorted(pairs), truncated
