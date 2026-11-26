"""Free-text name detection: trusted aliases, gazetteer, and honorific/relation cues.

Three independent routes feed the same span list (route A/B/C from the
blueprint): (A) aliases trusted because they came from a structured
patient/relative field earlier in the batch, (B) the bundled gazetteer, and
(C) honorific/relation/patient-label cue patterns. Overlapping hits are
merged, keeping the strongest evidence tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..ingest.header_aliases import is_field_label
from ..gazetteer import is_known_first_name, is_known_surname, skeleton_matches_gazetteer
from ..schema import DetectorEvidence, FieldRole
from ..translit import consonant_skeleton
from . import identifiers as ident
from .clinical import find_care_of_markers, find_clinical_history_markers, find_clinical_measurements

_LATIN_WORD = r"[A-Z][a-zA-Z']*\.?"
_DEVA_WORD = r"[ऀ-ॿ]+"

_LABEL_CUES = [
    (re.compile(r"\b(?:Patient|Pt\.?|Naam)\b\s*[:\-]?\s*", re.I), FieldRole.PATIENT_NAME, "LABEL_PATIENT"),
    (re.compile(r"नाम\s*[:\-]?\s*"), FieldRole.PATIENT_NAME, "LABEL_PATIENT_HI"),
]
_PROVIDER_PREFIX_CUES = [
    (re.compile(r"\bDr\.?\s+"), "PREFIX_DR"),
    (re.compile(r"डॉ\.?\s*"), "PREFIX_DR_HI"),
]
_RELATION_PREFIX_CUES = [
    (re.compile(r"\b(?:S/o|D/o|W/o)\b\s*[:\-]?\s*", re.I), "PREFIX_SDW"),
    (re.compile(r"\b(?:guardian|nominee|caretaker)\s*(?:name)?\s*[:\-]?\s*", re.I), "PREFIX_RELATION_LABEL"),
]
# Name-abbreviation prefix, not a pure honorific: "Md. Arif" is how "Mohammad
# Arif" is actually spelled, so the cue text itself is kept IN the span
# (see the dedicated loop below) so alias-family normalization still fires.
# Requiring prefix position directly before a capitalized word (no preceding
# comma) keeps this from firing on the postnominal degree "...Doe, MD".
_NAME_ABBREV_PREFIX_CUES = [
    (re.compile(r"\b(?:Md|Mohd)\.?(?=\s+[A-Z])\s*"), "PREFIX_MD_ABBREV"),
]
_HONORIFIC_PREFIX_CUES = [
    (re.compile(r"\b(?:Shri|Shree|Smt\.?)\s+"), "PREFIX_HONORIFIC"),
    (re.compile(r"श्री\s*|श्रीमती\s*"), "PREFIX_HONORIFIC_HI"),
]
# An English POSSESSIVE naming a relationship: the person is named BEFORE the
# apostrophe, so the capture window looks backwards. Lab-receipt remarks in the
# practice data are written exactly this way -- "Sample received from Kamal's
# attendant (W/o Mohan Pandey)" -- which named the patient with no label and no
# cue in front of it, and left 10 given names in clear. The kinship/role class
# after the possessive has no clinical reading.
# The name is captured by the pattern itself rather than by a backwards
# capture window: the window needs whitespace between the anchor and the name,
# and a possessive has none ("Kamal's", not "Kamal 's").
_POSSESSIVE_RELATION_CUES = [
    (re.compile(r"((?:[A-Z][\w'\-]*\s+){0,2}[A-Z][\w'\-]*)[\u2019']s\s+"
                r"(?:attendant|relative|guardian|escort|informant|caretaker|kin|"
                r"father|mother|son|daughter|husband|wife|brother|sister|"
                r"spouse|parent|child|family|next\s+of\s+kin)\b", re.I),
     "POSSESSIVE_RELATION"),
]
_HONORIFIC_SUFFIX_CUES = [
    (re.compile(r"\s+(?:sahab|sahib|saheb)\b", re.I), "SUFFIX_SAHAB"),
    (re.compile(r"\s+ji\b", re.I), "SUFFIX_JI"),
    # Kinship terms used as respectful address, which is how WhatsApp-style
    # referrals in the practice data name the patient: "Reena behenji",
    # "Shyam bhai", "Shahid babu", "Umesh Mama". The term follows the name, so
    # the capture window looks backwards. A closed class of address terms with
    # no clinical reading, exactly like the honorifics above.
    (re.compile(r"\s+(?:behen\s?ji|behenji|didi|bhabhi|bhaiya|bhai|dada|daada|"
                r"babu|baba|chacha|chachi|mama|mami|tau|taai|nana|nani|dadi|"
                r"amma|ammi|bibi|beta|beti)\b", re.I), "SUFFIX_KINSHIP"),
    (re.compile(r"\s+Devi\b"), "SUFFIX_DEVI"),
    (re.compile(r"\s*जी\b|\s*साहब\b"), "SUFFIX_HONORIFIC_HI"),
]

_STOPLIST = frozenset({
    "the", "this", "patient", "doctor", "hospital", "report", "blood", "pressure",
    "date", "name", "address", "ward", "diagnosis", "discharge", "admission",
    "opd", "history", "complaint", "summary", "prescription", "medication",
})

# Closed-class function words and common clinical-note vocabulary: gazetteer
# skeleton matching is coarse enough (2-4 consonants) that these otherwise
# collide with real name skeletons. Checked against the raw token, not the
# transliteration, since stoplist membership needs no fuzzy matching.
_DEVANAGARI_STOPLIST = frozenset({
    "को", "है", "का", "की", "के", "में", "पर", "से", "और", "यह", "वह", "हैं",
    "था", "थी", "थे", "हूँ", "कर", "गया", "गयी", "गए", "किया", "हुआ", "रहा",
    "रही", "रहे", "लिया", "दिया", "भर्ती", "बुखार", "दर्द", "खांसी", "दवा",
    "इलाज", "अस्पताल", "मधुमेह", "वार्ड", "पता",
    # Number-words and label fragments. "सं" / "संख्या" / "क्रमांक" mean
    # "number"; they stand next to an identifier label, and claiming them as
    # names both destroyed the label (so the identifier after it was no longer
    # recognised as labelled) and published it as "[REDACTED_NAME].:".
    "सं", "संख्या", "क्रमांक", "नं", "आईडी", "दिनांक", "समय",
    # Measure and duration words. Also caught structurally when a numeral
    # precedes them (see _NUMERAL_BEFORE), but they appear without one too.
    "दिन", "माह", "महीना", "महीने", "सप्ताह", "साल", "वर्ष", "घंटे", "घंटा",
})


# Generic ORGANISATION-TYPE words. A capitalised run that ends in one of these
# is an institution's name, not a person's -- "Jeevan Jyoti Charitable
# Hospital", "Arogya Seva Sadan", "Pathkind Diagnostics". The words are the
# generic type nouns only (never a particular facility's brand words), so the
# rule transfers to hospitals this corpus does not contain.
#
# This matters for identity, not only for privacy: a facility banner repeats on
# EVERY document a hospital issues, so a gazetteer hit inside it ("Jyoti" is a
# real given name) becomes a name token shared by every patient of that
# hospital and silently bridges them into one cluster.
_ORGANISATION_TYPES = (
    "hospital", "hospitals", "clinic", "clinics", "nursing home", "polyclinic",
    "medical college", "health centre", "health center", "phc", "chc",
    "dispensary", "sadan", "ashram", "trust", "society", "foundation",
    "diagnostics", "diagnostic centre", "diagnostic center", "pathology",
    "laboratory", "laboratories", "labs", "imaging", "scan centre",
    "institute", "centre", "center", "clinic & research", "healthcare",
    "multispeciality", "multispecialty", "superspeciality", "pharmacy",
    "chemists", "chemist", "medicals", "medical store", "blood bank",
)
_ORGANISATION_SEQUENCE = re.compile(
    r"\b(?:[A-Z][\w'&.]*\s+){1,6}(?:" + "|".join(
        t.replace(" ", r"\s+") for t in sorted(_ORGANISATION_TYPES, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)

# A Devanagari word directly preceded by a number is a unit or measure word,
# not a name: "65 वर्ष" (years), "6 दिन" (days), "500 मि.ग्रा." (mg). Those
# words transliterate onto real-name consonant skeletons, and because every
# clinical document states a duration, admitting them hands identity
# resolution a "name" token that every patient shares.
# Horizontal whitespace only, and at most a couple of spaces: `\s` matches a
# NEWLINE, so a date ending the previous line ("तारीख: 11-10-2026") made the
# first word of the next line look like a measure word and suppressed the
# patient's own name in the sentence body. A unit follows its number on the
# same line, immediately.
_NUMERAL_BEFORE = re.compile(r"[0-9\u0966-\u096F][ \t]{0,2}\Z")


def find_organisation_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of institution names -- see `_ORGANISATION_TYPES`."""
    return [(m.start(), m.end()) for m in _ORGANISATION_SEQUENCE.finditer(text)]


# Which relative a RELATIVE_NAME span names. Indian registration data uses the
# father's or husband's name as the standard disambiguator between two people
# who share a full name, so the relationship TYPE has to travel with the name:
# comparing a father's name on one record against a spouse's name on another
# would manufacture a disagreement out of two compatible facts.
#
# S/o and D/o both name the father, W/o the husband; the Hindi forms label the
# relationship directly. Read from the value's own prefix first (structured
# columns in the practice data store "W/o Sukhwinder Singh" in a single
# "Pita/Pati ka Naam" cell, i.e. the column cannot say which it is -- only the
# value can) and otherwise from the text immediately before the span.
RELATION_SPOUSE = "spouse"
RELATION_PARENT = "parent"

_RELATION_PREFIX_IN_VALUE = [
    (re.compile(r"^\s*[wW]\s*/\s*[oO]\b[\s.:,-]*"), RELATION_SPOUSE),
    (re.compile(r"^\s*[sSdD]\s*/\s*[oO]\b[\s.:,-]*"), RELATION_PARENT),
    (re.compile(r"^\s*(?:पत्नी|पति)\s*(?:का|की)?\s*(?:नाम)?\s*[\s.:,-]*"), RELATION_SPOUSE),
    (re.compile(r"^\s*(?:पिता|माता)\s*(?:का|की)?\s*(?:नाम)?\s*[\s.:,-]*"), RELATION_PARENT),
]
_RELATION_MARKER_BEFORE = [
    (re.compile(r"[wW]\s*/\s*[oO]\b[\s.:,()-]*$"), RELATION_SPOUSE),
    (re.compile(r"[sSdD]\s*/\s*[oO]\b[\s.:,()-]*$"), RELATION_PARENT),
    (re.compile(r"(?:पत्नी|पति)\s*(?:का|की)?\s*(?:नाम)?\s*[\s.:,()-]*$"), RELATION_SPOUSE),
    (re.compile(r"(?:पिता|माता)\s*(?:का|की)?\s*(?:नाम)?\s*[\s.:,()-]*$"), RELATION_PARENT),
]
# How far back a relationship marker may sit. Wide enough for "W/o " plus
# punctuation and a Hindi label, narrow enough that an unrelated marker
# earlier in the sentence is not attributed to this span.
_RELATION_LOOKBEHIND = 24


def relation_for_relative(value: str, preceding_text: str) -> str | None:
    """Which relative `value` names, or None when nothing says."""
    for pattern, relation in _RELATION_PREFIX_IN_VALUE:
        if pattern.match(value):
            return relation
    window = preceding_text[-_RELATION_LOOKBEHIND:]
    for pattern, relation in _RELATION_MARKER_BEFORE:
        if pattern.search(window):
            return relation
    return None


def strip_relation_prefix(value: str) -> str:
    """`value` with a leading relationship marker removed, so the NAME compares
    as a name ("W/o Sukhwinder Singh" -> "Sukhwinder Singh")."""
    for pattern, _relation in _RELATION_PREFIX_IN_VALUE:
        match = pattern.match(value)
        if match:
            return value[match.end():]
    return value


@dataclass
class NameSpan:
    start: int
    end: int
    text: str
    role: FieldRole
    tier: str
    rule_id: str


_NAME_PATTERN = re.compile(f"(?:{_LATIN_WORD}|{_DEVA_WORD})")


def _gap_is_name_continuation(gap: str) -> bool:
    """Only plain spaces/tabs may separate tokens of the same name span --
    never a newline, which means crossing into a different line/sentence
    (e.g. a section header) rather than continuing one name.
    """
    return gap.strip() == "" and "\n" not in gap


def _word_at(text: str, idx: int, forward: bool) -> tuple[int, int] | None:
    if forward:
        m = _NAME_PATTERN.search(text, idx)
        if m and m.start() - idx < 3 and _gap_is_name_continuation(text[idx:m.start()]):
            return m.start(), m.end()
        return None
    # backward: find the nearest word whose end is at/just before idx
    best = None
    for m in _NAME_PATTERN.finditer(text, max(0, idx - 40), idx + 1):
        if m.end() <= idx and idx - m.end() < 3 and _gap_is_name_continuation(text[m.end():idx]):
            best = (m.start(), m.end())
    return best


def _capture_window(text: str, anchor_end: int, forward: bool, max_tokens: int = 2) -> tuple[int, int] | None:
    pos = anchor_end
    start = end = None
    for _ in range(max_tokens):
        w = _word_at(text, pos, forward=forward)
        if w is None:
            break
        if forward:
            if start is None:
                start = w[0]
            end = w[1]
            pos = w[1]
        else:
            if end is None:
                end = w[1]
            start = w[0]
            pos = w[0]
        if pos < 0:
            break
    if start is None:
        return None
    return start, end


def _overlaps(a: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(a[0] < b[1] and b[0] < a[1] for b in spans)


ALL_NAME_ROUTES = frozenset({"alias", "cue", "gazetteer", "dictionary_free"})


@lru_cache(maxsize=8)
def _compiled_alias_patterns(alias_items: tuple[tuple[str, FieldRole], ...]) -> tuple[tuple[re.Pattern, FieldRole], ...]:
    """One combined alternation pattern per role, instead of a separate
    re.escape()+re.compile()+finditer() per alias. The caller's `trusted_aliases`
    dict is built ONCE per batch (plan.builder.analyze_ingest_result) and then
    passed, unchanged, into this function's caller for EVERY record in that
    batch -- re-escaping and re-compiling every alias on every single record
    (the original approach) is O(records x aliases) regex compiles, which is
    quadratic in patient count for a batch where both records and aliases grow
    with the number of patients. `lru_cache` keyed on the alias set's own
    content (not the dict's object identity, which is not a safe cache key
    across a long-running process) means the expensive compile step runs
    exactly ONCE per distinct alias set, no matter how many records reuse it;
    every subsequent record for the same batch gets an O(1) cache hit and then
    just TWO finditer() calls (one per role) instead of up to hundreds."""
    by_role: dict[FieldRole, list[str]] = {}
    for alias, role in alias_items:
        by_role.setdefault(role, []).append(alias)
    patterns: list[tuple[re.Pattern, FieldRole]] = []
    for role, aliases in by_role.items():
        # Longest-first: within one combined alternation, regex tries
        # branches in order, so a longer alias that CONTAINS a shorter one
        # (e.g. "Mohammad Arif Khan" vs "Mohammad Arif") is preferred.
        ordered = sorted(set(aliases), key=len, reverse=True)
        combined = "|".join(re.escape(a) for a in ordered)
        patterns.append((re.compile(combined, re.I), role))
    return tuple(patterns)


def detect_free_text_names(
    text: str,
    trusted_aliases: dict[str, FieldRole] | None = None,
    enabled_routes: frozenset[str] = ALL_NAME_ROUTES,
    corpus_name_tokens: frozenset[str] = frozenset(),
) -> list[NameSpan]:
    """enabled_routes lets a caller run a genuine detector ablation (e.g. the
    alias-only vs alias+gazetteer+cues comparison in scripts/eval_gate1.py)
    by actually disabling routes B/C, rather than post-hoc filtering a
    single merged run by evidence tier. Defaults to every route -- no
    production behavior change."""
    trusted_aliases = trusted_aliases or {}
    spans: list[NameSpan] = []
    excluded: list[tuple[int, int]] = []

    for start, end, _kind in find_clinical_measurements(text):
        excluded.append((start, end))
    for start, end in find_clinical_history_markers(text):
        excluded.append((start, end))

    # Route A: trusted batch aliases (case-insensitive whole-token match).
    if "alias" in enabled_routes and trusted_aliases:
        alias_items = tuple(sorted((a, r) for a, r in trusted_aliases.items() if a.strip()))
        for pattern, role in _compiled_alias_patterns(alias_items):
            for m in pattern.finditer(text):
                spans.append(NameSpan(m.start(), m.end(), m.group(0), role, "STRUCTURED_ANCHOR", "TRUSTED_ALIAS"))

    # Route C: cue patterns.
    if "cue" in enabled_routes:
        for pattern, role, rule_id in _LABEL_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.end(), forward=True)
                if w:
                    spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], role, "CUE", rule_id))
        for pattern, rule_id in _PROVIDER_PREFIX_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.end(), forward=True)
                if w:
                    spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.PROVIDER_NAME, "CUE", rule_id))
        for pattern, rule_id in _RELATION_PREFIX_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.end(), forward=True)
                if w:
                    spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.RELATIVE_NAME, "CUE", rule_id))
        for pattern, rule_id in _NAME_ABBREV_PREFIX_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.end(), forward=True, max_tokens=1)
                if w:
                    spans.append(NameSpan(m.start(), w[1], text[m.start():w[1]], FieldRole.PATIENT_NAME, "CUE", rule_id))
        for pattern, rule_id in _HONORIFIC_PREFIX_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.end(), forward=True)
                if w:
                    spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.PATIENT_NAME, "CUE", rule_id))
        for pattern, rule_id in _HONORIFIC_SUFFIX_CUES:
            for m in pattern.finditer(text):
                w = _capture_window(text, m.start(), forward=False)
                if w:
                    spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.PATIENT_NAME, "CUE", rule_id))
        for pattern, rule_id in _POSSESSIVE_RELATION_CUES:
            for m in pattern.finditer(text):
                spans.append(NameSpan(
                    m.start(1), m.end(1), m.group(1), FieldRole.PATIENT_NAME, "CUE", rule_id,
                ))
        for start, end, verdict in find_care_of_markers(text):
            if verdict != "NAME_FOLLOWS":
                continue
            if any(start < h_end and h_start < end for h_start, h_end in excluded):
                continue  # covered by a broader K/C/O or H/O history marker
            w = _capture_window(text, end, forward=True)
            if w:
                spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.RELATIVE_NAME, "CUE", "ADDRESS_CARE_OF"))

    cue_spans = [(s.start, s.end) for s in spans]
    # Institution banners are excluded from the undirected sweeps only: an
    # explicit cue ("Patient: ...") inside one is still trusted, because the
    # cue says what the value IS.
    organisation_spans = find_organisation_spans(text)

    # Route B: bundled gazetteer sweep over remaining tokens.
    if "gazetteer" in enabled_routes:
        for m in re.finditer(_LATIN_WORD, text):
            token = m.group(0).rstrip(".")
            if token.lower() in _STOPLIST or len(token) < 3:
                continue
            if is_field_label(token):
                continue  # a field LABEL is not a value -- see is_field_label
            span = (m.start(), m.end())
            if _overlaps(span, excluded) or _overlaps(span, cue_spans) or _overlaps(span, organisation_spans):
                continue
            if is_known_first_name(token) or is_known_surname(token):
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "GAZETTEER_HIT"))
            elif token.lower() in corpus_name_tokens:
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "CORPUS_NAME_TOKEN"))
        for m in re.finditer(_DEVA_WORD, text):
            token = m.group(0)
            if token in _DEVANAGARI_STOPLIST or is_field_label(token):
                continue
            span = (m.start(), m.end())
            if _overlaps(span, excluded) or _overlaps(span, cue_spans) or _overlaps(span, organisation_spans):
                continue
            if _NUMERAL_BEFORE.search(text[max(0, span[0] - 4):span[0]]):
                continue  # a measure word, not a name -- see _NUMERAL_BEFORE
            # Devanagari -> Latin transliteration varies in vowel quality (e.g. "aarif" vs
            # "arif"), so cross-script gazetteer recall uses skeleton equality, not an
            # exact string match. It is a candidate signal only, same as everywhere else.
            # A length floor keeps 1-consonant skeletons (common short postpositions)
            # from colliding with real name skeletons.
            skeleton = consonant_skeleton(token)
            if len(skeleton) >= 2 and skeleton_matches_gazetteer(skeleton):
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "GAZETTEER_HIT_DEVA"))
            elif token in corpus_name_tokens or (len(skeleton) >= 2 and skeleton in corpus_name_tokens):
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "CORPUS_NAME_TOKEN_DEVA"))

    # Routes D/E: dictionary-free recognition, run LAST so a name any stronger
    # route already claimed is left exactly as that route classified it.
    if "dictionary_free" in enabled_routes:
        spans.extend(detect_dictionary_free_names(
            text, list(excluded) + organisation_spans, [(s.start, s.end) for s in spans],
        ))

    return _merge_overlapping(spans, text)


_TIER_RANK = {"STRUCTURED_ANCHOR": 3, "CUE": 2, "GAZETTEER": 1}


def _merge_overlapping(spans: list[NameSpan], source_text: str) -> list[NameSpan]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s.start, -s.end))
    merged: list[NameSpan] = [spans[0]]
    for s in spans[1:]:
        last = merged[-1]
        if s.start < last.end:  # overlap
            new_start = min(last.start, s.start)
            new_end = max(last.end, s.end)
            best = s if _TIER_RANK[s.tier] > _TIER_RANK[last.tier] else last
            merged[-1] = NameSpan(new_start, new_end, source_text[new_start:new_end], best.role, best.tier, best.rule_id)
        else:
            merged.append(s)
    return merged


def evidence_for(span: NameSpan) -> DetectorEvidence:
    return DetectorEvidence(rule_id=span.rule_id, tier=span.tier, detail_code=f"NAME_{span.tier}")


def confidence_tier_for(tier: str) -> str:
    return {"STRUCTURED_ANCHOR": "HIGH", "CUE": "HIGH", "GAZETTEER": "MEDIUM"}.get(tier, "LOW")


# ---------------------------------------------------------------------------
# Route D / E: dictionary-INDEPENDENT name recognition
# ---------------------------------------------------------------------------
#
# Routes A-C all need the name to be either already known (a trusted alias), in
# the bundled gazetteer, or introduced by a naming label ("Patient:", "S/o",
# "Dr."). The organizer's planted canaries are built specifically to defeat all
# three: they are real-looking Indian names drawn from regions the gazetteer
# does not cover, used with no naming label at all --
#
#     "18:40 - Zorawar Qadri (attendant, bed 12), mob 6910522119"
#     "AMB-436,Sitapur,Lucknow,called by Kaveri Nambiar 94054-71434"
#
# No regex or gazetteer can reliably recognise an arbitrary unlabelled name, and
# pretending otherwise is how a detector starts redacting diagnoses. What CAN be
# recognised without a dictionary is the COMPANY the name keeps:
#
#   Route D -- an agent cue. "called by", "witnessed by", "brought by",
#     "attendant", "informant" name a PERSON performing a role. The cue is a
#     closed class and has no clinical reading, so what follows it is a name.
#
#   Route E -- identifier adjacency. A capitalised multi-token sequence sharing
#     a LINE with a direct identifier (a phone, an email, an Aadhaar) is a
#     person being contacted. Scoped to the line rather than a character window
#     so a name on one line cannot be condemned by a phone number on another,
#     which is what keeps "Dr. Ramesh Srivastava took evening round." (a value
#     the utility score expects to survive) out of range of the phone on the
#     line above it.
#
# Both routes are deliberately conservative about WHAT they claim: at least two
# adjacent capitalised tokens. A single bare capitalised word on a line with a
# phone number is far more often a column label, a place or a drug than a name,
# and claiming it would cost clinical text for no measurable privacy gain.

_AGENT_CUES = [
    # Someone performing an action on a patient's behalf.
    (re.compile(r"\b(?:called|phoned|contacted|brought|accompanied|escorted|witnessed|"
                r"signed|reported|raised|attended|referred|admitted|informed)\s+by\b\s*[:\-]?\s*", re.I), "AGENT_BY"),
    (re.compile(r"\b(?:attendant|visitor|informant|escort|caller|relative|companion|"
                r"next\s+of\s+kin|contact\s+person|guardian)\b\s*(?:name)?\s*[:\-]\s*", re.I), "AGENT_ROLE_LABELLED"),
]
# The same role words as a BARE parenthetical/appositive after a name
# ("Zorawar Qadri (attendant, bed 12)") -- the cue follows the name instead of
# preceding it, so the capture window looks backwards.
_AGENT_SUFFIX_CUES = [
    (re.compile(r"\s*\(\s*(?:attendant|visitor|informant|escort|caller|relative|companion|guardian)\b", re.I),
     "AGENT_ROLE_APPOSITIVE"),
    (re.compile(r"\s*,\s*(?:attendant|visitor|informant|escort|caller|guardian)\b", re.I),
     "AGENT_ROLE_APPOSITIVE"),
]

# A run of >=3 ALL-CAPS tokens is a document banner or a hospital name
# ("JEEVAN JYOTI CHARITABLE HOSPITAL, BARABANKI"), not a person.
_ALL_CAPS_RUN = re.compile(r"^[A-Z][A-Z'.]*(?:\s+[A-Z][A-Z'.]*){2,}$")
# Two or more adjacent capitalised Latin words.
_CAPITALISED_SEQUENCE = re.compile(r"\b[A-Z][a-zA-Z']{1,}\.?(?:\s+[A-Z][a-zA-Z']{1,}\.?){1,3}")
# Words that look like names by shape but are structural, geographic or
# clinical. Checked per token; a sequence containing one is not claimed.
_SEQUENCE_STOPLIST = frozenset({
    "dr", "mr", "mrs", "ms", "shri", "smt", "patient", "name", "mobile", "phone",
    "contact", "address", "ward", "bed", "room", "date", "time", "age", "sex",
    "gender", "uhid", "mrn", "ipd", "opd", "adm", "reg", "no", "number", "id",
    # Generic institution and document-structure words only. Particular
    # facility brand words ("Jyoti", "Seva") are deliberately NOT listed:
    # they are real given names, so excluding them by spelling would both
    # miss real patients called that and fail on any hospital this corpus
    # does not contain. Institution names are excluded structurally
    # instead -- see find_organisation_spans.
    "hospital", "clinic", "centre", "center",
    "discharge", "summary", "admission", "diagnosis", "history", "advice",
    "consultant", "department", "medicine", "surgery", "review", "follow",
    "blood", "sugar", "pressure", "random", "fasting", "serum", "creatinine",
    "haemoglobin", "hemoglobin", "pulse", "temp", "weight", "height", "spo",
    "tab", "inj", "cap", "syp", "oxygen", "route", "given", "stable", "fever",
    "visitor", "log", "attendant", "informant", "escort", "guardian", "relative",
    "mob", "tel", "whatsapp", "forward", "report", "result", "test", "sample",
    "lab", "amb", "trip", "remarks", "from", "to", "called", "by", "witnessed",
    "consent", "form", "surgical", "referral", "ticket", "billing", "query",
})


def _line_bounds(text: str, index: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return start, len(text) if end == -1 else end


def _sequence_is_plausible_person(candidate: str) -> bool:
    if _ALL_CAPS_RUN.match(candidate.strip()):
        return False
    tokens = [t for t in re.split(r"\s+", candidate.strip()) if t]
    if len(tokens) < 2:
        return False
    for token in tokens:
        bare = token.strip(".'").lower()
        if len(bare) < 2 or bare in _SEQUENCE_STOPLIST or bare in _STOPLIST:
            return False
        if any(ch.isdigit() for ch in token):
            return False
    return True


def _direct_identifier_spans(text: str) -> list[tuple[int, int]]:
    """Spans of values that are identifiers by FORMAT alone -- no dictionary
    and no label needed, so this works on completely unseen data."""
    spans: list[tuple[int, int]] = []
    spans.extend(ident.find_phone(text))
    spans.extend(ident.find_email(text))
    spans.extend(ident.find_abha_number(text))
    spans.extend((s, e) for s, e, *_ in ident.find_aadhaar_like(text))
    return spans


def detect_dictionary_free_names(
    text: str,
    excluded: list[tuple[int, int]],
    already_claimed: list[tuple[int, int]],
) -> list[NameSpan]:
    """Routes D and E. `excluded` are clinical spans that must never be
    reclassified; `already_claimed` are spans a stronger route already took."""
    spans: list[NameSpan] = []

    def _claim(start: int, end: int, rule_id: str, role: FieldRole) -> None:
        candidate = text[start:end]
        if not _sequence_is_plausible_person(candidate):
            return
        if _overlaps((start, end), excluded) or _overlaps((start, end), already_claimed):
            return
        spans.append(NameSpan(start, end, candidate, role, "CUE", rule_id))

    # Route D: an agent cue naming a person.
    for pattern, rule_id in _AGENT_CUES:
        for m in pattern.finditer(text):
            match = _CAPITALISED_SEQUENCE.match(text, m.end())
            if match:
                _claim(match.start(), match.end(), rule_id, FieldRole.RELATIVE_NAME)
    for pattern, rule_id in _AGENT_SUFFIX_CUES:
        for m in pattern.finditer(text):
            # The name precedes the cue: take the capitalised sequence that
            # ends exactly where the cue begins.
            line_start, _ = _line_bounds(text, m.start())
            best = None
            for match in _CAPITALISED_SEQUENCE.finditer(text, line_start, m.start()):
                if match.end() == m.start():
                    best = match
            if best:
                _claim(best.start(), best.end(), rule_id, FieldRole.RELATIVE_NAME)

    # Route E: a capitalised sequence sharing a line with a direct identifier.
    identifier_spans = _direct_identifier_spans(text)
    if identifier_spans:
        scanned_lines: set[tuple[int, int]] = set()
        for id_start, _id_end in identifier_spans:
            bounds = _line_bounds(text, id_start)
            if bounds in scanned_lines:
                continue
            scanned_lines.add(bounds)
            line_start, line_end = bounds
            for match in _CAPITALISED_SEQUENCE.finditer(text, line_start, line_end):
                if _overlaps((match.start(), match.end()), identifier_spans):
                    continue
                _claim(match.start(), match.end(), "NAME_ADJACENT_TO_IDENTIFIER", FieldRole.PATIENT_NAME)
    return spans
