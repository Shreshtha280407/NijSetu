"""Explainable evidence-tier scoring for a fuzzy candidate pair.

Thresholds (config.ScoringConfig) were picked against the synthetic
development fixtures in tests/fixtures/dev and then frozen -- see
PHASE1_REPORT.md for the fixtures used and why each threshold holds. Missing
evidence is ignored, never counted as agreement (a missing DOB on one side
is not a DOB match).
"""

from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path

from rapidfuzz import fuzz

from ..config import ScoringConfig
from ..schema import PatientCandidateFeatures
from ..translit import token_sequence_similarity

# Cross-script transliteration varies in vowel length ("arif" vs "aarif" from
# मोहम्मद आरिफ़) without being a different name; a per-token fuzzy match
# keeps those sitting in the same name instead of reading as a disagreement,
# while a real different name (e.g. "kumar" vs "chandra", ~33% ratio) stays
# well below this floor.
_TOKEN_EQUIV_FLOOR = 85.0


def _tokens_equivalent(t1: str, t2: str) -> bool:
    return t1 == t2 or fuzz.ratio(t1, t2) >= _TOKEN_EQUIV_FLOOR


def _fuzzy_subset(small: frozenset[str], big: frozenset[str]) -> bool:
    return all(any(_tokens_equivalent(s, b) for b in big) for s in small)

# A shared top-level source directory ("note-group"/episode clue) is weak
# corroborating provenance for an otherwise-bare name match -- not proof, but
# enough to nudge a genuine tie (see identity.cluster's anchor-size bonus for
# the stronger half of this: preferring an already-anchored cluster).
_SAME_BATCH_BONUS = 10.0


def _batch_scope(f: PatientCandidateFeatures) -> str | None:
    return Path(f.source_files[0]).parts[0] if f.source_files else None


@dataclass
class PairScore:
    score: float
    tier: str
    reasons: list[str]


def _is_name_disagreement(tokens_a: frozenset[str], tokens_b: frozenset[str], name_sim: float) -> bool:
    """True when the two names actively disagree (a differing second token,
    e.g. "Ramesh Kumar" vs "Ramesh Chandra") rather than one simply being a
    shorter, less-complete version of the other ("Arif" vs "Mohammad Arif").
    A missing token is absence of evidence; a different token is evidence
    against the match, and must never be waved through as "sparse but
    distinct" -- that exception is for information gaps, not contradictions.
    """
    if name_sim >= 90:
        return False
    is_subset = _fuzzy_subset(tokens_a, tokens_b) or _fuzzy_subset(tokens_b, tokens_a)
    return not is_subset


def score_pair(
    a: PatientCandidateFeatures,
    b: PatientCandidateFeatures,
    config: ScoringConfig,
) -> PairScore:
    reasons: list[str] = []
    name_a = " ".join(a.normalized_name_tokens)
    name_b = " ".join(b.normalized_name_tokens)
    name_sim = token_sequence_similarity(name_a, name_b) if name_a and name_b else 0.0

    tokens_a, tokens_b = frozenset(a.normalized_name_tokens), frozenset(b.normalized_name_tokens)
    disagreement = _is_name_disagreement(tokens_a, tokens_b, name_sim)
    # A single bare common name matching another single bare common name
    # (e.g. "Ramesh" vs "Ramesh") carries no more than the first-name overlap
    # itself -- no distinguishing content at all.
    is_distinctive = len(tokens_a | tokens_b) >= 2

    dob_match = a.birth_year is not None and a.birth_year == b.birth_year
    if dob_match:
        reasons.append("DOB_YEAR_MATCH")

    shared_phone = bool(set(a.phone_digits) & set(b.phone_digits))
    shared_address = bool(set(a.address_tokens) & set(b.address_tokens))
    # Requires both sides to actually have a first+last structure: a single
    # bare token (e.g. a cue-only "Arif") equaling the last token of a fuller
    # name is a partial NAME match, not a shared-family-surname signal.
    shared_surname = (
        len(a.normalized_name_tokens) >= 2 and len(b.normalized_name_tokens) >= 2
        and a.normalized_name_tokens[-1] == b.normalized_name_tokens[-1]
    )
    household_hit = shared_phone or shared_address or shared_surname
    if household_hit:
        reasons.append("HOUSEHOLD_SHARED")

    same_batch = _batch_scope(a) is not None and _batch_scope(a) == _batch_scope(b)
    if same_batch:
        reasons.append("SAME_NOTE_GROUP")

    score = name_sim * 0.7
    if dob_match:
        score += 15.0
    household_score = config.household_signal_cap if household_hit else 0.0
    score += household_score
    if same_batch:
        score += _SAME_BATCH_BONUS

    if disagreement:
        # Household/phone signals may still corroborate two DIFFERENT family
        # members sharing contact details, but a disagreeing name must never
        # be bridged into "the same person" through name evidence alone.
        tier = "CORRELATED_HOUSEHOLD" if household_hit and name_sim >= 60 else "AMBIGUOUS"
    elif dob_match:
        tier = "CORROBORATED_FUZZY"
    elif is_distinctive:
        tier = "SPARSE_BUT_DISTINCT"
    elif household_hit and name_sim >= 60:
        tier = "CORRELATED_HOUSEHOLD"
    else:
        tier = "AMBIGUOUS"

    reasons.append(f"NAME_SIM_{round(name_sim)}")
    return PairScore(score=score, tier=tier, reasons=reasons)
