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

from ..gazetteer import is_known_first_name, is_known_surname, skeleton_matches_gazetteer
from ..schema import DetectorEvidence, FieldRole
from ..translit import consonant_skeleton
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
_HONORIFIC_SUFFIX_CUES = [
    (re.compile(r"\s+(?:sahab|sahib|saheb)\b", re.I), "SUFFIX_SAHAB"),
    (re.compile(r"\s+ji\b", re.I), "SUFFIX_JI"),
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
})


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


ALL_NAME_ROUTES = frozenset({"alias", "cue", "gazetteer"})


def detect_free_text_names(
    text: str,
    trusted_aliases: dict[str, FieldRole] | None = None,
    enabled_routes: frozenset[str] = ALL_NAME_ROUTES,
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
    if "alias" in enabled_routes:
        for alias, role in trusted_aliases.items():
            if not alias.strip():
                continue
            for m in re.finditer(re.escape(alias), text, re.I):
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
        for start, end, verdict in find_care_of_markers(text):
            if verdict != "NAME_FOLLOWS":
                continue
            if any(start < h_end and h_start < end for h_start, h_end in excluded):
                continue  # covered by a broader K/C/O or H/O history marker
            w = _capture_window(text, end, forward=True)
            if w:
                spans.append(NameSpan(w[0], w[1], text[w[0]:w[1]], FieldRole.RELATIVE_NAME, "CUE", "ADDRESS_CARE_OF"))

    cue_spans = [(s.start, s.end) for s in spans]

    # Route B: bundled gazetteer sweep over remaining tokens.
    if "gazetteer" in enabled_routes:
        for m in re.finditer(_LATIN_WORD, text):
            token = m.group(0).rstrip(".")
            if token.lower() in _STOPLIST or len(token) < 3:
                continue
            span = (m.start(), m.end())
            if _overlaps(span, excluded) or _overlaps(span, cue_spans):
                continue
            if is_known_first_name(token) or is_known_surname(token):
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "GAZETTEER_HIT"))
        for m in re.finditer(_DEVA_WORD, text):
            token = m.group(0)
            if token in _DEVANAGARI_STOPLIST:
                continue
            span = (m.start(), m.end())
            if _overlaps(span, excluded) or _overlaps(span, cue_spans):
                continue
            # Devanagari -> Latin transliteration varies in vowel quality (e.g. "aarif" vs
            # "arif"), so cross-script gazetteer recall uses skeleton equality, not an
            # exact string match. It is a candidate signal only, same as everywhere else.
            # A length floor keeps 1-consonant skeletons (common short postpositions)
            # from colliding with real name skeletons.
            skeleton = consonant_skeleton(token)
            if len(skeleton) >= 2 and skeleton_matches_gazetteer(skeleton):
                spans.append(NameSpan(span[0], span[1], token, FieldRole.PATIENT_NAME, "GAZETTEER", "GAZETTEER_HIT_DEVA"))

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
