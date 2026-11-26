"""Patient identity resolution: scoped exact matches first, then blocked fuzzy candidates."""

from __future__ import annotations

import re
from datetime import datetime

from ..config import ScoringConfig, resolve_configured_issuer
from ..schema import Feature, FieldRole, LinkDecision, PatientCandidateFeatures, ScopedIdentifier
from ..translit import consonant_skeleton, tokenize_name, vowel_collapsed
from .candidates import generate_candidate_pairs
from .cluster import ClusterResolver
from .scoped import build_scoped_index, exact_match_edges

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")


def _parse_year(text: str) -> int | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).year
        except ValueError:
            continue
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


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
    name_tokens: list[str] = []
    for f in name_features:
        name_tokens.extend(tokenize_name(f.original_text))

    birth_year = None
    for f in features:
        if f.role == FieldRole.DATE:
            year = _parse_year(f.original_text)
            if year is not None:
                birth_year = year
                break

    phone_digits = tuple(
        re.sub(r"\D", "", f.original_text)
        for f in features
        if f.role == FieldRole.CONTACT and len(re.sub(r"\D", "", f.original_text)) >= 7
    )
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
    scoped_ids = tuple(
        ScopedIdentifier(
            issuer=f.issuer or fallback_issuer,
            scheme=f.scheme, value=f.original_text, source=f.source,
        )
        for f in features
        if f.role == FieldRole.PATIENT_IDENTIFIER and f.scheme and f.original_text.strip()
    )

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
