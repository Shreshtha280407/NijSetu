"""FHIR R4 JSON adapter: declared subset only (Patient + linked Encounter/Observation/...).

Routes on resourceType, supports a collection Bundle, and records reference
evidence (fullUrl / relative "Type/id" / "urn:uuid:" / "#contained") without
ever resolving an external URL. Broad profile validation is out of Phase 1
scope by design (see blueprint page 06/12).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..config import Limits
from ..errors import LimitExceededError, MalformedInputError
from ..schema import Field, FileRecord, FieldRole, ScopedIdentifier, SourceCoordinate, ValueType, Disposition
from .encoding import detect_and_decode
from .json_adapter import _dup_key_hook, _scan_depth

SUPPORTED_RESOURCE_TYPES = {
    "Patient", "Encounter", "Observation", "Condition", "Procedure",
    "MedicationRequest", "DiagnosticReport", "Practitioner", "Bundle",
}

_NAME_ROLE_BY_RESOURCE = {
    "Patient": FieldRole.PATIENT_NAME,
    "Practitioner": FieldRole.PROVIDER_NAME,
}


@dataclass
class Reference:
    source: SourceCoordinate
    raw: str
    kind: str  # "relative" / "urn_uuid" / "contained" / "fullurl" / "unsupported"
    field_key: str  # "subject" / "encounter" / "performer" -- which field this came from
    resolved: bool = False
    target_resource_no: int | None = None  # which parsed FhirResource this points at, once resolved


@dataclass
class FhirResource:
    resource_no: int
    resource_type: str
    logical_id: str | None
    full_url: str | None
    fields: list[Field] = field(default_factory=list)
    scoped_identifiers: list[ScopedIdentifier] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    # Patient association resolved from a "subject" reference -- deliberately
    # kept separate from resource identity (logical_id/full_url): a clinical
    # record that references a Patient is linked to that patient's identity,
    # it never becomes that Patient resource. None of this is set from
    # "performer"/"encounter" references, so a Practitioner/Encounter
    # reference can never establish a patient-identity merge.
    patient_subject_resource_no: int | None = None
    patient_subject_ambiguous: bool = False


@dataclass
class FhirResult:
    file_record: FileRecord
    resources: list[FhirResource]
    held_resources: list[tuple[int, list[str]]] = field(default_factory=list)  # (entry_ordinal, reason_codes)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _emit_scalar(
    resource_no: int, rel_path: str, path: str, value: Any, role: FieldRole, out: list[Field],
    scheme: str | None = None,
) -> None:
    vtype = ValueType.STRING
    if isinstance(value, bool):
        vtype = ValueType.BOOLEAN
    elif isinstance(value, int):
        vtype = ValueType.INTEGER
    elif isinstance(value, Decimal):
        vtype = ValueType.DECIMAL
    src = SourceCoordinate(file_no=0, file_path=rel_path, record_no=resource_no, path=path)
    out.append(Field(source=src, value_type=vtype, value=value, original_text=_text(value), role=role, scheme=scheme))


def _classify_reference(raw: str) -> str:
    if raw.startswith("#"):
        return "contained"
    if raw.startswith("urn:uuid:"):
        return "urn_uuid"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "unsupported"
    if "/" in raw and not raw.startswith("/"):
        return "relative"
    return "unsupported"


def _extract_resource(obj: dict, resource_no: int, rel_path: str, full_url: str | None) -> FhirResource:
    rtype = obj.get("resourceType", "")
    res = FhirResource(
        resource_no=resource_no,
        resource_type=rtype,
        logical_id=obj.get("id"),
        full_url=full_url,
    )
    name_role = _NAME_ROLE_BY_RESOURCE.get(rtype, FieldRole.OTHER)

    for ident in obj.get("identifier", []) or []:
        if not isinstance(ident, dict):
            continue
        system = ident.get("system")
        value = ident.get("value")
        if value is not None:
            # An explicit identifier.system is authoritative scope and must
            # survive into identity resolution unchanged (both issuer and
            # scheme keyed off it, so an exact match requires the same
            # system string on both sides). Absent a system, this still
            # needs *some* scheme so build_candidate_features can apply its
            # normal conservative fallback-issuer logic (facility / explicit
            # --source-system config / per-file unknown scope) -- it must
            # never be silently dropped from scoped identity evidence.
            src = SourceCoordinate(file_no=0, file_path=rel_path, record_no=resource_no, path="identifier.value")
            scheme = system if system else "fhir_identifier"
            res.fields.append(Field(
                source=src, value_type=ValueType.STRING, value=value,
                original_text=_text(value), role=FieldRole.PATIENT_IDENTIFIER,
                issuer=system, scheme=scheme,
            ))
            res.scoped_identifiers.append(ScopedIdentifier(
                issuer=system or "UNSCOPED", scheme=scheme, value=str(value), source=src,
            ))

    for name_obj in obj.get("name", []) or []:
        if not isinstance(name_obj, dict):
            continue
        if name_obj.get("text"):
            _emit_scalar(resource_no, rel_path, "name.text", name_obj["text"], name_role, res.fields)
        for given in name_obj.get("given", []) or []:
            _emit_scalar(resource_no, rel_path, "name.given", given, name_role, res.fields)
        if name_obj.get("family"):
            _emit_scalar(resource_no, rel_path, "name.family", name_obj["family"], name_role, res.fields)

    if obj.get("birthDate"):
        _emit_scalar(resource_no, rel_path, "birthDate", obj["birthDate"], FieldRole.DATE, res.fields, scheme="dob")

    # Encounter.period is the FHIR carrier for an inpatient admission/
    # discharge pair -- tagging start/end with the SAME scheme ("encounter_date")
    # made them collide in extract_assertions (only the first occurrence per
    # scheme is kept), so the discharge end was silently dropped from the
    # interval-duration invariant and the required 5-day-stay check never
    # actually ran against FHIR. Any other resource's `period` keeps the
    # original single-event scheme (no admission/discharge semantics implied).
    period = obj.get("period") if isinstance(obj.get("period"), dict) else None
    if period and period.get("start"):
        start_scheme = "admission_date" if rtype == "Encounter" else "encounter_date"
        _emit_scalar(resource_no, rel_path, "period.start", period["start"], FieldRole.DATE, res.fields, scheme=start_scheme)
    if period and period.get("end"):
        end_scheme = "discharge_date" if rtype == "Encounter" else "encounter_date"
        _emit_scalar(resource_no, rel_path, "period.end", period["end"], FieldRole.DATE, res.fields, scheme=end_scheme)
    if obj.get("effectiveDateTime"):
        date_scheme = "report_date" if rtype == "DiagnosticReport" else "specimen_date" if rtype == "Observation" else "encounter_date"
        _emit_scalar(resource_no, rel_path, "effectiveDateTime", obj["effectiveDateTime"], FieldRole.DATE, res.fields, scheme=date_scheme)
    if obj.get("issued"):
        _emit_scalar(resource_no, rel_path, "issued", obj["issued"], FieldRole.DATE, res.fields, scheme="report_date")
    if obj.get("authoredOn"):
        _emit_scalar(resource_no, rel_path, "authoredOn", obj["authoredOn"], FieldRole.DATE, res.fields, scheme="encounter_date")
    if obj.get("performedDateTime"):
        _emit_scalar(resource_no, rel_path, "performedDateTime", obj["performedDateTime"], FieldRole.DATE, res.fields, scheme="encounter_date")

    for telecom in obj.get("telecom", []) or []:
        if isinstance(telecom, dict) and telecom.get("value"):
            _emit_scalar(resource_no, rel_path, "telecom.value", telecom["value"], FieldRole.CONTACT, res.fields)

    for addr in obj.get("address", []) or []:
        if not isinstance(addr, dict):
            continue
        for line in addr.get("line", []) or []:
            _emit_scalar(resource_no, rel_path, "address.line", line, FieldRole.FINE_ADDRESS, res.fields)
        if addr.get("district"):
            _emit_scalar(resource_no, rel_path, "address.district", addr["district"], FieldRole.COARSE_GEOGRAPHY, res.fields)
        if addr.get("postalCode"):
            _emit_scalar(resource_no, rel_path, "address.postalCode", addr["postalCode"], FieldRole.FINE_ADDRESS, res.fields)

    for key in ("subject", "encounter", "performer"):
        ref_obj = obj.get(key)
        candidates = ref_obj if isinstance(ref_obj, list) else ([ref_obj] if ref_obj else [])
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            raw_ref = cand.get("reference")
            if cand.get("display"):
                _emit_scalar(resource_no, rel_path, f"{key}.display", cand["display"], FieldRole.OTHER, res.fields)
            if raw_ref:
                src = SourceCoordinate(file_no=0, file_path=rel_path, record_no=resource_no, path=f"{key}.reference")
                kind = _classify_reference(raw_ref)
                res.references.append(Reference(source=src, raw=raw_ref, kind=kind, field_key=key))

    text_div = obj.get("text", {}).get("div") if isinstance(obj.get("text"), dict) else None
    if text_div:
        _emit_scalar(resource_no, rel_path, "text.div", text_div, FieldRole.OTHER, res.fields)

    for key in ("code", "valueQuantity", "dosageInstruction", "medicationCodeableConcept"):
        if key in obj:
            _emit_scalar(resource_no, rel_path, key, json.dumps(obj[key], default=str), FieldRole.CLINICAL_MEASUREMENT, res.fields)

    return res


def _contained_ids_with_counts(resource: dict) -> dict[str, int]:
    """id -> occurrence count among this resource's own `contained` entries.
    A count > 1 means the owner itself declared the same id twice -- that id
    must never resolve (ambiguous), not pick one arbitrarily."""
    counts: dict[str, int] = {}
    for contained in resource.get("contained", []) or []:
        if isinstance(contained, dict) and contained.get("id"):
            counts[contained["id"]] = counts.get(contained["id"], 0) + 1
    return counts


def parse_fhir_tree(raw: bytes, limits: Limits) -> dict:
    """Parses raw FHIR JSON bytes into the raw (lossless) Python dict --
    the whole document, Bundle or bare resource, without any field
    extraction. Shared by parse_fhir (Phase 1 identity/reference graph) and
    Phase 2's whitelist-reconstruction rewriter, which needs the real raw
    resource dicts (not Phase 1's partial Field extraction) to inspect and
    transform every surface explicitly rather than copying unknown content."""
    if len(raw) > limits.max_json_bytes:
        raise LimitExceededError("JSON_SIZE_EXCEEDED")
    text, _enc = detect_and_decode(raw)
    _scan_depth(text, limits.max_json_depth)
    try:
        data = json.loads(text, object_pairs_hook=_dup_key_hook, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise MalformedInputError("JSON_SYNTAX_ERROR", detail=str(exc)) from None
    if not isinstance(data, dict) or "resourceType" not in data:
        raise MalformedInputError("FHIR_RESOURCE_TYPE_MISSING")
    return data


def parse_fhir(raw: bytes, rel_path: str, limits: Limits, file_no: int = 0) -> FhirResult:
    data = parse_fhir_tree(raw, limits)
    rtype = data["resourceType"]
    resources: list[FhirResource] = []
    held_resources: list[tuple[int, list[str]]] = []
    # owner's own resource_no -> {contained_id: occurrence_count}; a
    # #contained reference only ever resolves within its OWNING resource's
    # own list -- the same id declared under a different owner is a
    # different resource entirely and must never cross-resolve (Finding 3).
    contained_by_owner: dict[int, dict[str, int]] = {}

    if rtype == "Bundle":
        entries = data.get("entry", []) or []
        for ordinal, entry in enumerate(entries):
            if not isinstance(entry, dict):
                held_resources.append((ordinal, ["FHIR_ENTRY_MALFORMED"]))
                continue
            resource = entry.get("resource")
            if not isinstance(resource, dict) or "resourceType" not in resource:
                held_resources.append((ordinal, ["FHIR_ENTRY_MISSING_RESOURCE"]))
                continue
            res_rtype = resource["resourceType"]
            if res_rtype not in SUPPORTED_RESOURCE_TYPES:
                held_resources.append((ordinal, ["FHIR_RESOURCE_TYPE_UNSUPPORTED_IN_BUNDLE"]))
                continue
            extracted = _extract_resource(resource, ordinal, rel_path, entry.get("fullUrl"))
            contained_by_owner[ordinal] = _contained_ids_with_counts(resource)
            resources.append(extracted)
        if entries and not resources:
            # Every entry was held/unsupported/malformed: this must read as
            # an explicit hold, never a silently "clean" zero-resource file.
            disposition = Disposition.HELD
            file_reasons = ["FHIR_NO_SUPPORTED_RESOURCES_IN_BUNDLE"]
        else:
            disposition = Disposition.PROCESSED
            file_reasons = ["FHIR_ENTRIES_HELD"] if held_resources else []
    elif rtype in SUPPORTED_RESOURCE_TYPES:
        extracted = _extract_resource(data, 0, rel_path, None)
        contained_by_owner[0] = _contained_ids_with_counts(data)
        resources.append(extracted)
        disposition = Disposition.PROCESSED
        file_reasons = []
    else:
        raise MalformedInputError("FHIR_RESOURCE_TYPE_UNSUPPORTED")

    # resource_no is the Bundle.entry ordinal (not the compacted list
    # position, since held/unsupported entries are never materialized into
    # `resources`), so target lookups must go through this map, not
    # `resources[i]`.
    by_resource_no: dict[int, FhirResource] = {r.resource_no: r for r in resources}

    full_url_index: dict[str, int] = {}
    duplicate_full_url: set[str] = set()
    logical_index: dict[str, int] = {}
    duplicate_logical: set[str] = set()
    for r in resources:
        if r.full_url:
            if r.full_url in full_url_index:
                duplicate_full_url.add(r.full_url)
            else:
                full_url_index[r.full_url] = r.resource_no
        if r.logical_id:
            logical_key = f"{r.resource_type}/{r.logical_id}"
            if logical_key in logical_index:
                duplicate_logical.add(logical_key)
            else:
                logical_index[logical_key] = r.resource_no

    for res in resources:
        for ref in res.references:
            if ref.kind == "contained":
                owner_contained = contained_by_owner.get(res.resource_no, {})
                target_id = ref.raw[1:]
                ref.resolved = owner_contained.get(target_id, 0) == 1
                # Contained resources are not modelled as separate
                # FhirResource entries (Phase 1 deliberately does not parse
                # into a contained resource's own fields) -- resolved means
                # "the reference target exists under its owner", not a
                # resource_no to point patient-association at.
            elif ref.kind == "urn_uuid":
                ref.resolved = ref.raw in full_url_index and ref.raw not in duplicate_full_url
                if ref.resolved:
                    ref.target_resource_no = full_url_index[ref.raw]
            elif ref.kind == "relative":
                ref.resolved = ref.raw in logical_index and ref.raw not in duplicate_logical
                if ref.resolved:
                    ref.target_resource_no = logical_index[ref.raw]

        # Patient association: only a "subject" reference that resolved to
        # an actual Patient resource counts -- never "performer" (typically
        # a Practitioner) or "encounter", and never an unresolved/ambiguous
        # target. Multiple distinct resolved Patient targets is an explicit
        # conflict, not an arbitrary pick of the first one.
        patient_targets = {
            ref.target_resource_no
            for ref in res.references
            if ref.field_key == "subject" and ref.resolved and ref.target_resource_no is not None
            and ref.target_resource_no != res.resource_no
            and by_resource_no[ref.target_resource_no].resource_type == "Patient"
        }
        if len(patient_targets) == 1:
            res.patient_subject_resource_no = next(iter(patient_targets))
        elif len(patient_targets) > 1:
            res.patient_subject_ambiguous = True

    return FhirResult(
        file_record=FileRecord(
            file_no=file_no, file_path=rel_path, format="fhir", disposition=disposition,
            record_count=len(resources), reason_codes=file_reasons,
        ),
        resources=resources,
        held_resources=held_resources,
    )
