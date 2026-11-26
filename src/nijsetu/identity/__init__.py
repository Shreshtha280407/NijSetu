"""Patient identity resolution: scoped exact matches first, then blocked fuzzy candidates."""

from __future__ import annotations

import re
from datetime import datetime

from ..config import ScoringConfig, resolve_configured_issuer
from ..ingest.header_aliases import EVENT_SCOPED_SCHEMES
from ..schema import Feature, FieldRole, LinkDecision, PatientCandidateFeatures, ScopedIdentifier
from ..detect.names import strip_relation_prefix
from ..translit import consonant_skeleton, tokenize_name, vowel_collapsed
from .candidates import generate_candidate_pairs
from .cluster import ClusterResolver
from .idkeys import canonical_identifier, issuer_prefix_of
from .scoped import build_scoped_index, exact_match_edges

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")

# Tokens that reach the name list through a free-text name hit but are
# structural or clinical vocabulary, not parts of a person's name. Excluded
# from the identity feature vector only -- detection still treats the span
# however it classified it; this is purely about not letting a lab panel name
# or a Hindi function word act as name evidence for linkage.
_NON_NAME_TOKENS = frozenset({
    "routine", "panel", "profile", "report", "test", "sample", "lab",
    "patient", "name", "ward", "bed", "room", "din", "pet", "dawai",
    "review", "weeks", "days", "time", "visit", "note", "notes",
})


def _parse_year(text: str) -> int | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).year
        except ValueError:
            continue
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


_MIN_COMPARABLE_PHONE_DIGITS = 7
_NATIONAL_NUMBER_DIGITS = 10


def _canonical_phone(text: str) -> str | None:
    """The national subscriber number, or None if there is not enough of one.

    An Indian mobile is ten digits; exports write it with and without the
    `+91` country code and with and without a trunk `0`. Keeping the LAST ten
    digits makes every spelling of one number compare equal, which is required
    both for a match to be found and -- more importantly -- for a genuine
    disagreement between two different numbers to be trustworthy.
    """
    digits = re.sub(r"\D", "", text)
    if len(digits) < _MIN_COMPARABLE_PHONE_DIGITS:
        return None
    return digits[-_NATIONAL_NUMBER_DIGITS:] if len(digits) >= _NATIONAL_NUMBER_DIGITS else digits


def resolve_fallback_issuer(
    features: list[Feature], source_files: tuple[str, ...], source_system_map: tuple[tuple[str, str], ...] = (),
) -> str:
    """Same priority order used for identity clustering's ScopedIdentifier
    scope, exposed so Phase 2 can tokenize a hospital-local identifier
    (UHID, MRN, billing id, ...) under the identical issuer scope it was
    actually clustered under -- a UHID field never carrying its own
    `Feature.issuer` must not fall back to NO issuer scope at token time,
    or two different hospitals' coincidentally-identical local IDs collide
    onto the same public token even though identity resolution correctly
    kept them as different patients."""
    facility_names = [f.original_text.strip().lower() for f in features if f.role == FieldRole.FACILITY_NAME and f.original_text.strip()]
    primary_source = source_files[0] if source_files else None
    configured_issuer = resolve_configured_issuer(primary_source, source_system_map) if primary_source else None
    if facility_names:
        return facility_names[0]
    if configured_issuer is not None:
        return configured_issuer
    return f"UNKNOWN_SOURCE:{primary_source}" if primary_source else "UNKNOWN_SOURCE"


def build_candidate_features(
    record_key: str,
    features: list[Feature],
    source_files: tuple[str, ...],
    source_system_map: tuple[tuple[str, str], ...] = (),
) -> PatientCandidateFeatures:
    name_features = sorted(
        (f for f in features if f.role == FieldRole.PATIENT_NAME),
        key=lambda f: f.char_start,
    )
    # De-duplicated, order-preserving. A name mentioned several times in one
    # record ("Sukhwinder ji ... Sukhwinder Kaur") previously contributed its
    # tokens repeatedly, which inflated token-sequence similarity and -- worse
    # -- corrupted the "shared surname" heuristic, since that reads the LAST
    # token of this list and a repeated given name can end up there. A lab
    # panel name ("Routine") landing in the list via a free-text name hit had
    # the same effect.
    name_tokens: list[str] = []
    seen_tokens: set[str] = set()
    for f in name_features:
        for token in tokenize_name(f.original_text):
            if token in _NON_NAME_TOKENS or token in seen_tokens:
                continue
            seen_tokens.add(token)
            name_tokens.append(token)

    # ONLY a date of birth may supply a birth year. This previously took the
    # first DATE feature of any kind, so a visit date became the "birth year"
    # -- an OPD visit in 2026 was recorded as born in 2026. That made
    # DOB_YEAR_MATCH fire between two records that merely shared a visit
    # month, and (worse) made a genuine DOB disagreement unusable as evidence
    # against a match, because one side's "birth year" was not a birth year at
    # all. The distinction matters now that a year MISMATCH is a veto.
    birth_year = None
    for f in features:
        if f.role == FieldRole.DATE and f.scheme == "dob":
            year = _parse_year(f.original_text)
            if year is not None:
                birth_year = year
                break

    # Canonicalised to the national subscriber number, so the SAME phone
    # written two ways compares equal. "+91 78044 94655" and "7804494655" are
    # one number; comparing raw digit strings made them look like two, which
    # both lost a corroborating match and -- once a phone disagreement became
    # a veto -- manufactured contradictions between a patient's own records.
    phone_digits = tuple(dict.fromkeys(
        canonical
        for f in features
        if f.role == FieldRole.CONTACT
        for canonical in [_canonical_phone(f.original_text)]
        if canonical
    ))
    address_tokens = tuple(
        tok for f in features if f.role in (FieldRole.FINE_ADDRESS, FieldRole.COARSE_GEOGRAPHY)
        for tok in re.findall(r"[a-zA-Z]+", f.original_text.lower())
    )
    # A hospital-local scheme (UHID, billing ID) is scoped, in priority order:
    #   1. An explicit authoritative scope the format itself carries (FHIR
    #      identifier.system, or a national scheme -- Aadhaar/ABHA -- which
    #      is fixed regardless of file; see detect.__init__._NATIONAL_SCHEMES).
    #   2. An explicit FACILITY_NAME field named in the record itself.
    #   3. A deliberately configured --source-system mapping for this file
    #      path (AppConfig.source_system_map) -- this is how two files that
    #      ARE genuinely the same issuer (or are deliberately kept distinct)
    #      get told so explicitly.
    #   4. Conservative fallback: an unknown issuer is scoped to this file
    #      alone, never shared across files by default. A folder name is
    #      not treated as evidence of a hospital/episode here -- it is only
    #      used as an opaque per-file partition key so two independently
    #      sourced, unconfigured files never silently collide on a repeated
    #      local identifier (e.g. two different hospitals both using
    #      "UHID 001"). Rows within the SAME file still share this scope,
    #      since they are, by construction, the same declared input source.
    fallback_issuer = resolve_fallback_issuer(features, source_files, source_system_map)
    # Only PATIENT-level identifiers become identity anchors. An event-level
    # identifier (specimen / episode / bill number) is excluded: one patient
    # legitimately owns many of them, so admitting them here turns normal
    # repeat activity into an ID_CONFLICT that vetoes a genuine merge. They
    # are still detected and tokenized for privacy -- this filter only
    # governs identity semantics. See header_aliases.EVENT_SCOPED_SCHEMES.
    scoped_ids = tuple(
        ScopedIdentifier(
            issuer=f.issuer or fallback_issuer,
            scheme=f.scheme, value=f.original_text, source=f.source,
        )
        for f in features
        if f.role == FieldRole.PATIENT_IDENTIFIER and f.scheme and f.original_text.strip()
        and f.scheme not in EVENT_SCOPED_SCHEMES
    )

    # Named relatives, keyed by WHICH relative (see detect.names.
    # relation_for_relative). A relative whose relationship is unstated is
    # skipped rather than guessed: comparing a father's name against an
    # unlabelled attendant's name is not evidence either way.
    relatives: dict[str, list[str]] = {}
    for f in features:
        if f.role != FieldRole.RELATIVE_NAME or not f.scheme:
            continue
        tokens = [
            t for t in tokenize_name(strip_relation_prefix(f.original_text))
            if t not in _NON_NAME_TOKENS
        ]
        if tokens:
            relatives.setdefault(f.scheme, []).extend(tokens)
    relative_names = tuple(
        (relation, tuple(dict.fromkeys(tokens)))
        for relation, tokens in sorted(relatives.items())
    )

    # The same identifier values, stripped of their issuer namespace. Built
    # from `scoped_ids` so the event-level exclusion above applies here too.
    identifier_keys = tuple(dict.fromkeys(
        (ident.scheme, issuer_prefix_of(ident.value) or "", canonical_identifier(ident.value))
        for ident in scoped_ids
        if canonical_identifier(ident.value)
    ))

    return PatientCandidateFeatures(
        record_key=record_key,
        normalized_name_tokens=tuple(name_tokens),
        skeletons=tuple(consonant_skeleton(t) for t in name_tokens),
        vowel_collapsed=tuple(vowel_collapsed(t) for t in name_tokens),
        birth_year=birth_year,
        phone_digits=phone_digits,
        address_tokens=address_tokens,
        scoped_ids=scoped_ids,
        source_files=source_files,
        identifier_keys=identifier_keys,
        relative_names=relative_names,
    )


def resolve_identities(
    record_features: dict[str, list[Feature]],
    record_sources: dict[str, tuple[str, ...]],
    scoring: ScoringConfig,
    max_candidate_bucket: int,
    source_system_map: tuple[tuple[str, str], ...] = (),
    fhir_patient_links: dict[str, str] | None = None,
    fhir_ambiguous_links: frozenset[str] = frozenset(),
) -> tuple[dict[str, LinkDecision], dict[str, int]]:
    """Returns (record_key -> LinkDecision, truncated-bucket diagnostics).

    fhir_patient_links: record_key -> the Patient record_key it is resolved
    to via a FHIR subject reference (same resource graph, not an identifier
    match) -- see ingest.fhir_adapter's per-resource patient_subject_resource_no.
    fhir_ambiguous_links: record_keys whose subject reference resolved to more
    than one distinct Patient resource -- held explicitly, never merged.
    """
    fhir_patient_links = fhir_patient_links or {}
    features_list = [
        build_candidate_features(key, feats, record_sources.get(key, ()), source_system_map)
        for key, feats in record_features.items()
    ]

    resolver = ClusterResolver(scoring)
    for cf in features_list:
        resolver.register(cf.record_key, cf.scoped_ids)

    scoped_records = {cf.record_key: list(cf.scoped_ids) for cf in features_list}
    scoped_index = build_scoped_index(scoped_records)
    for a, b, scope_label in exact_match_edges(scoped_index):
        resolver.attach_exact(a, b, scope_label)

    known_keys = {cf.record_key for cf in features_list}
    for record_key, patient_key in fhir_patient_links.items():
        if record_key in known_keys and patient_key in known_keys:
            resolver.attach_exact(record_key, patient_key, "FHIR_SUBJECT_REFERENCE")
    for record_key in fhir_ambiguous_links:
        if record_key in known_keys:
            resolver.flag_held(record_key, "FHIR_SUBJECT_REFERENCE_AMBIGUOUS")

    pairs, truncated = generate_candidate_pairs(features_list, max_candidate_bucket)
    resolver.attach_fuzzy_edges(features_list, pairs)

    return resolver.finalize(), truncated
