"""Unified detection pass: runs identifier/clinical/name detectors over one text span
and emits typed Features with original-offset source coordinates, whether the text
came from a structured field value or a whole free-text document.
"""

from __future__ import annotations

from dataclasses import replace

from ..ingest.header_aliases import resolve_header
from ..schema import DetectorEvidence, Feature, FieldRole, SourceCoordinate
from . import identifiers as ident
from .clinical import find_care_of_markers, find_clinical_history_markers, find_clinical_measurements
from .names import ALL_NAME_ROUTES, confidence_tier_for, detect_free_text_names, evidence_for

STRUCTURED_ROLES = frozenset({
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PROVIDER_NAME,
    FieldRole.FACILITY_NAME, FieldRole.PATIENT_IDENTIFIER, FieldRole.CONTACT,
    FieldRole.FINE_ADDRESS, FieldRole.COARSE_GEOGRAPHY, FieldRole.DATE,
})

# Aadhaar/ABHA are national schemes: the same value from two different files is
# the same scope. A UHID/MRN/billing ID is hospital-local -- its issuer is
# resolved later (identity.build_candidate_features), once the record's
# sibling fields (an explicit facility/hospital name, if present) are visible.
# These issuer strings must match exactly what the free-text identifier
# detectors (detect.identifiers, via _identifier_features below) assign for
# the same schemes -- otherwise a national ID detected in a structured field
# and the identical value detected in free text would land in different
# scope keys and never exact-match.
_NATIONAL_ISSUERS = {"aadhaar": "UIDAI", "abha_number": "ABDM", "abha_address": "ABDM"}
_NATIONAL_SCHEMES = frozenset(_NATIONAL_ISSUERS)


def _default_scheme(base_source: SourceCoordinate) -> str | None:
    last_segment = base_source.path.rsplit(".", 1)[-1].split("[")[0]
    canonical, role = resolve_header(last_segment)
    return canonical if role == FieldRole.PATIENT_IDENTIFIER else None


# Canonical date-kind names (dob / admission_date / discharge_date /
# encounter_date / specimen_date / report_date) -- carried in Feature.scheme
# exactly like a PATIENT_IDENTIFIER's scheme, so Phase 2's date-shift policy
# can distinguish DOB (-> age banding) from every other clinical timeline
# date (-> a per-patient shift) on the same structured-field mechanism.
_DATE_KINDS = frozenset({"dob", "admission_date", "discharge_date", "encounter_date", "specimen_date", "report_date"})


def _date_kind(base_source: SourceCoordinate) -> str | None:
    last_segment = base_source.path.rsplit(".", 1)[-1].split("[")[0]
    canonical, role = resolve_header(last_segment)
    return canonical if role == FieldRole.DATE and canonical in _DATE_KINDS else None


def _mk(base: SourceCoordinate, start: int, end: int) -> SourceCoordinate:
    offset = base.char_start or 0
    return replace(base, char_start=offset + start, char_end=offset + end)


def detect_in_text(
    text: str,
    base_source: SourceCoordinate,
    trusted_aliases: dict[str, FieldRole] | None = None,
    structured_role: FieldRole = FieldRole.OTHER,
    pre_issuer: str | None = None,
    pre_scheme: str | None = None,
    enabled_name_routes: frozenset[str] = ALL_NAME_ROUTES,
) -> list[Feature]:
    """pre_issuer/pre_scheme let an adapter that already knows the
    authoritative scope (FHIR identifier.system) hand it through untouched,
    instead of this function re-inferring it from the field's header alias
    (which FHIR field paths like "identifier.value" never match)."""
    features: list[Feature] = []

    if structured_role in STRUCTURED_ROLES and structured_role != FieldRole.OTHER and not text.strip():
        return features  # blank structured field: nothing to detect or scope

    if structured_role in STRUCTURED_ROLES and structured_role != FieldRole.OTHER:
        issuer, scheme = (None, None)
        if structured_role == FieldRole.PATIENT_IDENTIFIER:
            if pre_scheme is not None:
                scheme, issuer = pre_scheme, pre_issuer
            else:
                scheme = _default_scheme(base_source)
                issuer = _NATIONAL_ISSUERS.get(scheme) if scheme in _NATIONAL_SCHEMES else None
        elif structured_role == FieldRole.DATE:
            scheme = pre_scheme if pre_scheme is not None else _date_kind(base_source)
        features.append(Feature(
            source=_mk(base_source, 0, len(text)),
            role=structured_role,
            char_start=(base_source.char_start or 0),
            char_end=(base_source.char_start or 0) + len(text),
            original_text=text,
            evidence=[DetectorEvidence("STRUCTURED_FIELD", "STRUCTURED_ANCHOR", "FROM_HEADER_ALIAS")],
            confidence_tier="HIGH",
            issuer=issuer, scheme=scheme,
        ))
        return _attach_identifier_evidence(features, text, base_source)

    for start, end, kind in find_clinical_measurements(text):
        features.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.CLINICAL_MEASUREMENT,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence(kind, "FORMAT", "CLINICAL_PATTERN")],
            confidence_tier="HIGH",
        ))
    history_spans = find_clinical_history_markers(text)
    for start, end in history_spans:
        features.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.CLINICAL_HISTORY,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("HISTORY_MARKER", "FORMAT", "CLINICAL_HISTORY_MARKER")],
            confidence_tier="HIGH",
        ))
    for start, end, verdict in find_care_of_markers(text):
        if any(start < h_end and h_start < end for h_start, h_end in history_spans):
            continue  # already covered by a broader K/C/O or H/O marker
        if verdict == "CLINICAL_COMPLAINT":
            features.append(Feature(
                source=_mk(base_source, start, end), role=FieldRole.CLINICAL_HISTORY,
                char_start=start, char_end=end, original_text=text[start:end],
                evidence=[DetectorEvidence("CARE_OF_COMPLAINT", "FORMAT", "OPD_COMPLAINT_SHORTHAND")],
                confidence_tier="HIGH",
            ))
        # verdict == "NAME_FOLLOWS" is handled inside detect_free_text_names (Route C),
        # so it merges correctly with gazetteer/alias hits on the same span.

    features.extend(_identifier_features(text, base_source))

    for span in detect_free_text_names(text, trusted_aliases, enabled_name_routes):
        features.append(Feature(
            source=_mk(base_source, span.start, span.end), role=span.role,
            char_start=span.start, char_end=span.end, original_text=span.text,
            evidence=[evidence_for(span)],
            confidence_tier=confidence_tier_for(span.tier),
        ))

    return features


def _identifier_features(text: str, base_source: SourceCoordinate) -> list[Feature]:
    out: list[Feature] = []
    for start, end, kind, is_masked, checksum_ok in ident.find_aadhaar_like(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.PATIENT_IDENTIFIER,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence(kind, "FORMAT" if not checksum_ok else "CHECKSUM",
                                        "AADHAAR_MASKED" if is_masked else "AADHAAR_FORMAT")],
            issuer="UIDAI", scheme="aadhaar",
            confidence_tier="HIGH",
        ))
    for start, end in ident.find_abha_number(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.PATIENT_IDENTIFIER,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("ABHA_NUMBER", "FORMAT", "ABHA_NUMBER_FORMAT")],
            issuer="ABDM", scheme="abha_number", confidence_tier="MEDIUM",
        ))
    for start, end in ident.find_abha_address(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.PATIENT_IDENTIFIER,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("ABHA_ADDRESS", "FORMAT", "ABHA_ADDRESS_FORMAT")],
            issuer="ABDM", scheme="abha_address", confidence_tier="HIGH",
        ))
    for start, end in ident.find_phone(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.CONTACT,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("PHONE_IN", "FORMAT", "PHONE_FORMAT")],
            scheme="PHONE", confidence_tier="MEDIUM",
        ))
    for start, end in ident.find_email(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.CONTACT,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("EMAIL", "FORMAT", "EMAIL_FORMAT")],
            scheme="EMAIL", confidence_tier="HIGH",
        ))
    for start, end in ident.find_pin_code(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.FINE_ADDRESS,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("PIN_CODE", "FORMAT", "PIN_LABELLED")],
            confidence_tier="HIGH",
        ))
    for start, end in ident.find_labelled_uhid(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.PATIENT_IDENTIFIER,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("UHID_LABELLED", "FORMAT", "UHID_LABEL_ANCHOR")],
            scheme="uhid", confidence_tier="HIGH",
        ))
    for start, end, kind in ident.find_labelled_dates(text):
        out.append(Feature(
            source=_mk(base_source, start, end), role=FieldRole.DATE,
            char_start=start, char_end=end, original_text=text[start:end],
            evidence=[DetectorEvidence("LABELLED_DATE", "FORMAT", f"{kind.upper()}_LABEL_ANCHOR")],
            scheme=kind, confidence_tier="HIGH",
        ))
    return out


def _attach_identifier_evidence(features: list[Feature], text: str, base_source: SourceCoordinate) -> list[Feature]:
    """For already-labelled identifier fields, add format/checksum evidence to the same span."""
    if features and features[0].role == FieldRole.PATIENT_IDENTIFIER:
        for start, end, kind, is_masked, checksum_ok in ident.find_aadhaar_like(text):
            if start == 0 and end == len(text):
                features[0].evidence.append(DetectorEvidence(
                    kind, "CHECKSUM" if checksum_ok else "FORMAT",
                    "CHECKSUM_VALID" if checksum_ok else "CHECKSUM_NOT_VERIFIED",
                ))
                features[0].issuer = "UIDAI"
                features[0].scheme = "aadhaar"
    return features
