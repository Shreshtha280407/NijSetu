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

from ..translit.similarity import ratio as _ratio

from ..config import ScoringConfig
from ..schema import PatientCandidateFeatures
from ..translit import token_sequence_similarity
from .idkeys import informative_series

# Cross-script transliteration varies in vowel length ("arif" vs "aarif" from
# मोहम्मद आरिफ़) without being a different name; a per-token fuzzy match
# keeps those sitting in the same name instead of reading as a disagreement,
# while a real different name (e.g. "kumar" vs "chandra", ~33% ratio) stays
# well below this floor.
_TOKEN_EQUIV_FLOOR = 85.0


def _tokens_equivalent(t1: str, t2: str) -> bool:
    return t1 == t2 or _ratio(t1, t2) >= _TOKEN_EQUIV_FLOOR


def _fuzzy_subset(small: frozenset[str], big: frozenset[str]) -> bool:
    return all(any(_tokens_equivalent(s, b) for b in big) for s in small)


def _agreeing_token_count(tokens_a: frozenset[str], tokens_b: frozenset[str]) -> int:
    """How many of the SMALLER name's tokens have an equivalent in the larger.

    This is the amount of name evidence genuinely shared, which is the only
    thing that distinguishes one person from another who happens to share a
    given name. Counting the union instead treats a missing token as if it
    were a matching one.
    """
    small, big = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    return sum(1 for s in small if any(_tokens_equivalent(s, b) for b in big))

# A shared top-level source directory ("note-group"/episode clue) is weak
# corroborating provenance for an otherwise-bare name match -- not proof, but
# enough to nudge a genuine tie (see identity.cluster's anchor-size bonus for
# the stronger half of this: preferring an already-anchored cluster).
_SAME_BATCH_BONUS = 10.0


def _batch_scope(f: PatientCandidateFeatures) -> str | None:
    return Path(f.source_files[0]).parts[0] if f.source_files else None


# An identifier value agreeing between two records is the strongest non-name
# evidence available, but it is only usable when coincidental agreement is
# implausible. A canonical value carrying at least this many digits spans a
# space of >=10^4 minted numbers, so two unrelated patients sharing one is not
# a plausible accident; below it the "identifier" is more likely a ward code, a
# bed number or a form revision than a patient number.
_MIN_DISTINCTIVE_ID_DIGITS = 4


def _is_distinctive_identifier(canonical: str) -> bool:
    return sum(c.isdigit() for c in canonical) >= _MIN_DISTINCTIVE_ID_DIGITS


def _identifier_agreement(a: PatientCandidateFeatures, b: PatientCandidateFeatures) -> bool:
    """True when both records carry the SAME distinctive identifier value under
    the same scheme, regardless of which issuer namespace each was scoped to.

    Namespace scoping is deliberately conservative: an unconfigured file gets
    its own issuer, so the same real UHID arriving in a hospital's registration
    export and in a central lab's bulk export cannot form an authoritative
    exact edge. Treating the value agreement as *corroborating* evidence
    recovers that link without claiming the two files are the same issuer --
    and because it only ever raises the evidence tier for a pair whose names
    already agree, it cannot bridge two different patients who happen to share
    a recycled local id (see `_is_name_disagreement`).
    """
    keys_a = {(scheme, value) for scheme, _prefix, value in a.identifier_keys if _is_distinctive_identifier(value)}
    keys_b = {(scheme, value) for scheme, _prefix, value in b.identifier_keys if _is_distinctive_identifier(value)}
    return bool(keys_a & keys_b)


def _relative_conflict(a: PatientCandidateFeatures, b: PatientCandidateFeatures) -> bool:
    """True when both records name the SAME relative role and the names differ.

    This is the last discriminator available for a genuine homonym. The
    organizer plants exactly that case: two patients both called Sukhwinder
    Kaur, one registered W/o Sukhwinder Singh and one W/o Kuldeep Singh. Their
    names agree completely, their UHIDs sit in different namespaces (so the
    scoped veto cannot fire), and the nursing note that mentions the second one
    carries no phone or DOB to contradict the first with. The husband's name is
    the only evidence that separates them -- which is precisely why Indian
    registration data records it.

    Roles are compared like with like: a father's name on one side and a
    spouse's on the other are two compatible facts, not a disagreement, and a
    relative whose role is unstated contributes nothing (see
    detect.names.relation_for_relative).

    Agreement is judged by `_is_name_disagreement`, exactly as for the patient's
    own name, so one definition of "these two names disagree" governs the whole
    module. Requiring merely SOME shared token is not enough and was measured
    not to be: "Kuldeep Singh" and "Sukhwinder Singh" share the surname Singh,
    which is one of the most common surnames in the region, and that alone
    cleared the veto on the organizer's planted homonym pair.
    """
    by_relation_b = dict(b.relative_names)
    for relation, tokens_a in a.relative_names:
        tokens_b = by_relation_b.get(relation)
        if not tokens_a or not tokens_b:
            continue
        set_a, set_b = frozenset(tokens_a), frozenset(tokens_b)
        similarity = token_sequence_similarity(" ".join(tokens_a), " ".join(tokens_b))
        if _is_name_disagreement(set_a, set_b, similarity):
            return True
    return False


def _series_conflict(a: PatientCandidateFeatures, b: PatientCandidateFeatures) -> bool:
    """True when both records hold an identifier from the SAME facility
    numbering series under the same scheme, and the numbers differ.

    One facility issues one patient number per patient, so two different
    numbers out of one series are two different patients. This is the evidence
    that separates homonyms the issuer-scoped veto cannot reach: that veto
    needs both values in one proven namespace, and a bulk export scoped to its
    own file shares a namespace with nobody.

    Only an *informative* series counts -- see `informative_series`: a prefix
    that merely repeats the scheme's own name ("UHID-0001") identifies no
    facility, so two hospitals both formatting ids that way must not be read
    as one series.
    """
    series_a: dict[tuple[str, str], set[str]] = {}
    for scheme, prefix, value in a.identifier_keys:
        series = informative_series(scheme, prefix)
        if series is not None:
            series_a.setdefault((scheme, series), set()).add(value)
    if not series_a:
        return False
    for scheme, prefix, value in b.identifier_keys:
        series = informative_series(scheme, prefix)
        if series is None:
            continue
        peers = series_a.get((scheme, series))
        if peers and value not in peers:
            return True
    return False


@dataclass
class PairScore:
    score: float
    tier: str
    reasons: list[str]
    # True when the pair's name evidence is a single agreeing token, i.e. too
    # weak to merge on its own. identity.cluster gates these behind the
    # runner-up margin instead of rejecting them -- see `score_pair`.
    provisional: bool = False


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
    # Distinctiveness is graded by how many tokens actually AGREE, not by how
    # many distinct tokens the two names contain between them.
    #
    # TWO agreeing tokens ("Mohammad Arif" vs "Md. Arif") is a name specific
    # enough to stand on its own. ONE agreeing token ("Sukhwinder" vs
    # "Sukhwinder Kaur") is the weakest name evidence there is -- a bare given
    # name -- and on the practice data it accepted exactly the organizer's
    # homonym trap: two different patients, one at Jeevan Jyoti (JJH-0077058)
    # and one at Sanjeevani (SNJ/26/064423), both called Sukhwinder, merged
    # into one pseudonym. A cross-issuer identifier disagreement cannot veto
    # that merge, because one person legitimately holds different local UHIDs
    # at different hospitals.
    #
    # It is still admissible, because a bare mention IS how free-text notes
    # refer to patients and refusing it costs real recall (an OPD note reading
    # "Arif sahab ka BP ..." has nothing else to link by). What makes it safe
    # is that a one-token match is marked PROVISIONAL: identity.cluster
    # requires such an edge to beat every competing cluster by the runner-up
    # margin, so it merges when the patient is unambiguous and abstains when
    # two rival patients fit equally well -- which is the actual difference
    # between the Arif case and the Sukhwinder trap, since no amount of name
    # scoring can separate two identical names.
    agreeing_tokens = _agreeing_token_count(tokens_a, tokens_b)
    is_distinctive = agreeing_tokens >= 1
    provisional = agreeing_tokens < 2 or len(tokens_a) < 2 or len(tokens_b) < 2

    dob_match = a.birth_year is not None and a.birth_year == b.birth_year
    if dob_match:
        reasons.append("DOB_YEAR_MATCH")

    shared_phone = bool(set(a.phone_digits) & set(b.phone_digits))
    shared_address = bool(set(a.address_tokens) & set(b.address_tokens))
    identifier_match = _identifier_agreement(a, b)
    if identifier_match:
        reasons.append("IDENTIFIER_VALUE_MATCH")

    # Explicit CONTRADICTIONS. Absent evidence is correctly ignored elsewhere in
    # this module, but PRESENT-AND-DIFFERENT evidence is not absence -- it is
    # evidence against the match, and the only thing that separates two people
    # who genuinely share a full name.
    #
    # This is the organizer's homonym trap. Two different patients are both
    # called "Sukhwinder Kaur", one at Jeevan Jyoti (JJH-0077058) and one at
    # Sanjeevani (SNJ/26/064423). Their names agree completely, so no amount of
    # name scoring can separate them, and the scoped-identifier veto cannot
    # fire either: the identifiers sit in different issuer namespaces, and a
    # real person legitimately holds different local UHIDs at different
    # hospitals. What does separate them is that both carry a phone number and
    # the numbers differ.
    #
    # Both sides must actually HAVE the evidence for a contradiction to count,
    # so a sparse record is never penalised for what it does not say.
    # A DOB disagreement is authoritative: two different birth years mean two
    # different people, and nothing outweighs it.
    birth_year_conflict = (
        a.birth_year is not None and b.birth_year is not None and a.birth_year != b.birth_year
    )
    # A phone disagreement is strong but NOT absolute -- one patient
    # legitimately has a personal and a relative's number across records -- so
    # it is overridable by an authoritative birth-year agreement. Phones are
    # compared canonically (see identity._canonical_phone), so this fires only
    # on genuinely different numbers, never on two spellings of one.
    phone_conflict = bool(a.phone_digits) and bool(b.phone_digits) and not shared_phone
    # Two different numbers from one facility's series. Overridable by an
    # authoritative birth-year agreement for the same reason as phone: a
    # genuine duplicate registration at one hospital does produce two local
    # ids for one patient, and that is a merge we WANT to make. An agreeing
    # identifier value on some other scheme also clears it, since direct
    # identifier evidence outranks an inference from a shared prefix.
    series_conflict = _series_conflict(a, b)
    relative_conflict = _relative_conflict(a, b)
    contradictions: list[str] = []
    if birth_year_conflict:
        contradictions.append("BIRTH_YEAR_CONFLICT")
    if phone_conflict and not dob_match:
        contradictions.append("PHONE_CONFLICT")
    if series_conflict and not dob_match and not identifier_match:
        contradictions.append("ID_SERIES_CONFLICT")
    # Not overridable by an agreeing identifier VALUE: that agreement is itself
    # an inference across unproven namespaces, whereas two different husbands
    # is a direct statement about two different people. A birth-year agreement
    # does override it, since a shared DOB plus a shared name plus a
    # contradicting relative is far more likely to be one patient with a
    # miskeyed relative field than two people alike in every other respect.
    if relative_conflict and not dob_match:
        contradictions.append("RELATIVE_NAME_CONFLICT")
    reasons.extend(contradictions)
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
    if identifier_match:
        score += config.identifier_value_signal
    household_score = config.household_signal_cap if household_hit else 0.0
    score += household_score
    if same_batch:
        score += _SAME_BATCH_BONUS

    if contradictions:
        # A contradiction is never outweighed by similarity. Held explicitly so
        # the reason codes say WHICH evidence conflicted, rather than the pair
        # quietly scoring below a floor.
        tier = "AMBIGUOUS"
    elif disagreement:
        # Household/phone signals may still corroborate two DIFFERENT family
        # members sharing contact details, but a disagreeing name must never
        # be bridged into "the same person" through name evidence alone.
        tier = "CORRELATED_HOUSEHOLD" if household_hit and name_sim >= 60 else "AMBIGUOUS"
    elif dob_match or identifier_match:
        tier = "CORROBORATED_FUZZY"
    elif (
        a.identifier_keys and b.identifier_keys
        and not shared_phone and not shared_address
    ):
        # Both records state a patient number and NONE of them agree. The name
        # is then the only thing supporting the merge, and a name -- even an
        # identical full name -- is not an identifier: two patients called
        # Kamal Rajput at two hospitals look exactly like one patient who
        # attends both. Neither reading is supported, so the system abstains
        # instead of picking one.
        #
        # Deliberately NOT a contradiction: it is absence of corroboration, not
        # evidence of distinctness. A record with no identifier at all (a ward
        # note) is unaffected -- there the name is all that exists, and the
        # runner-up margin governs it.
        tier = "AMBIGUOUS"
        reasons.append("NO_IDENTIFIER_CORROBORATION")
    elif provisional and not (same_batch or household_hit):
        # One agreeing name token and nothing else. A bare given name, on its
        # own, does not identify a person -- two different patients called
        # Sukhwinder produce a 100%-similar name match, and so do two
        # unrelated notes that each say only "Arif sahab". Measured on the
        # practice corpus, accepting these is what let an unrelated bare
        # mention become the anchor for a whole cluster.
        #
        # Provenance (same note group) or a household signal is enough to make
        # the mention attributable; a DOB or identifier agreement lifts the
        # pair out of provisional entirely, above.
        tier = "AMBIGUOUS"
        reasons.append("UNCORROBORATED_BARE_NAME")
    elif is_distinctive:
        tier = "SPARSE_BUT_DISTINCT"
    elif household_hit and name_sim >= 60:
        tier = "CORRELATED_HOUSEHOLD"
    else:
        tier = "AMBIGUOUS"

    reasons.append(f"NAME_SIM_{round(name_sim)}")
    # An agreeing identifier VALUE is direct evidence about which patient
    # this is, so it lifts the pair out of provisional even when the name
    # on one side is a bare mention.
    return PairScore(score=score, tier=tier, reasons=reasons, provisional=provisional and not identifier_match)
