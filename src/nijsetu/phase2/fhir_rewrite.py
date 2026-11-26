"""FHIR R4 whitelist-reconstruction rewriter.

Builds the sanitized resource from scratch using only a declared, inspected
set of fields -- anything not in the whitelist (meta, extension,
modifierExtension, contained, narrative, _primitive companions, ...) is
never copied into the output, which is what guarantees an uninspected
surface can never leak: by construction, only fields this module explicitly
names ever reach the sanitized Bundle.

A resource with `contained` or a non-empty `modifierExtension` is held
(never partially reconstructed) -- the two cases the task's spec explicitly
allows treating this way instead of recursive contained-subtree sanitization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..detect import detect_in_text
from ..ingest.fhir_adapter import SUPPORTED_RESOURCE_TYPES, _extract_resource
from ..schema import Action, FieldRole, SourceCoordinate
from . import keys
from .actions import decide_feature_action
from .clinical_invariants import compare_assertions, extract_assertions
from .dates import DateParseError, parse_flexible_date, shift_date_text
from .patient_context import PatientContext
from .policy import PolicyConfig

_NAME_ROLE_BY_RESOURCE = {"Patient": FieldRole.PATIENT_NAME, "Practitioner": FieldRole.PROVIDER_NAME}
_DATE_SCALAR_FIELDS = ("effectiveDateTime", "issued", "authoredOn", "performedDateTime")
# Fields whose FHIR type is a plain scalar (enum string, Quantity) -- no
# embedded narrative text, safe to copy through unscanned.
_SCALAR_PASSTHROUGH_FIELDS = ("status", "intent", "priority", "valueQuantity")
# Fields typed as a single `Coding` (system/code/display) -- `display` is
# free text and must be scanned like any other narrative surface; being
# named in this whitelist is not, by itself, evidence the field's own
# content is safe.
_CODING_TYPED_FIELDS = ("class",)
# Fields typed as `CodeableConcept` (text + coding[].display) -- same
# reasoning: the field name is permitted, its *text*/`display` content still
# needs the same inspection as any other free-text surface.
_CODEABLE_CONCEPT_TYPED_FIELDS = (
    "code", "clinicalStatus", "verificationStatus", "valueCodeableConcept", "medicationCodeableConcept",
)
# Free text worth an embedded-name/identifier scan before retaining -- unlike
# the pure scalar passthrough fields above, these can carry clinician-authored
# prose that quotes a patient name or identifier (valueString is a free-text
# FHIR type, not a controlled vocabulary).
_CLINICAL_TEXT_SCAN_FIELDS = ("conclusion", "valueString")
# Coding sub-fields that are never free text -- display is handled separately.
_CODING_PASSTHROUGH_SUBFIELDS = ("system", "version", "code", "userSelected")


@dataclass
class FhirEntryResult:
    resource_no: int
    resource_type: str
    identity: str  # deterministic UUID string, used for both `id` and fullUrl/reference targets
    sanitized: dict | None  # None when held
    actions: list[Action] = field(default_factory=list)
    hold_reasons: list[str] = field(default_factory=list)


def iter_bundle_resources(tree: dict) -> tuple[str, list[tuple[int, dict]]]:
    """Returns (top_resourceType, [(resource_no, raw_resource_dict), ...]),
    using the same resource_no=Bundle.entry-ordinal convention as parse_fhir
    so results correlate 1:1 with Phase 1's identity/reference graph."""
    rtype = tree.get("resourceType", "")
    if rtype == "Bundle":
        out = []
        for ordinal, entry in enumerate(tree.get("entry", []) or []):
            if isinstance(entry, dict) and isinstance(entry.get("resource"), dict):
                res = entry["resource"]
                if res.get("resourceType") in SUPPORTED_RESOURCE_TYPES:
                    out.append((ordinal, res))
        return rtype, out
    return rtype, [(0, tree)]


def _scan_text(text: str) -> list:
    if not text:
        return []
    base = SourceCoordinate(file_no=0, file_path="", record_no=-1, path="<fhir_text>")
    return detect_in_text(text, base)


def _mask_or_retain_text(
    text: str, policy: PolicyConfig, patient_ctx: PatientContext, release_key: bytes, target: str,
) -> tuple[str, list[Action]]:
    from .spans import apply_span_replacements, resolve_overlaps
    feats = _scan_text(text)
    if not feats:
        return text, []
    resolved = resolve_overlaps(feats)
    actions: list[Action] = []
    repls = []
    for f in resolved:
        result = decide_feature_action(f, policy, patient_ctx, release_key, target)
        repls.append((f.char_start, f.char_end, result.replacement_text))
        actions.append(result.action)
    return apply_span_replacements(text, repls), actions


def _sanitize_coding(
    coding: Any, policy: PolicyConfig, patient_ctx: PatientContext, release_key: bytes, target: str,
) -> tuple[dict | None, list[Action]]:
    """A single FHIR `Coding` (system/version/code/display/userSelected) --
    everything but `display` is a controlled-vocabulary scalar; `display` is
    free text and gets the same scan as any other narrative surface. An
    unsupported shape (not a dict) is safely omitted, never copied through."""
    if not isinstance(coding, dict):
        return None, []
    out = {k: coding[k] for k in _CODING_PASSTHROUGH_SUBFIELDS if k in coding}
    actions: list[Action] = []
    display = coding.get("display")
    if isinstance(display, str) and display.strip():
        new_text, scan_actions = _mask_or_retain_text(display, policy, patient_ctx, release_key, f"{target}.display")
        out["display"] = new_text
        actions.extend(scan_actions)
    return (out or None), actions


def _sanitize_codeable_concept(
    cc: Any, policy: PolicyConfig, patient_ctx: PatientContext, release_key: bytes, target: str,
) -> tuple[dict | None, list[Action]]:
    """A `CodeableConcept` (text + coding[]) -- `text` is free text (clinician
    narrative, e.g. a value description), each `coding[].display` likewise;
    `coding[].system`/`.code` are controlled vocabulary, retained verbatim.
    An unsupported shape is safely omitted, never copied through."""
    if not isinstance(cc, dict):
        return None, []
    out: dict[str, Any] = {}
    actions: list[Action] = []
    codings = cc.get("coding")
    if isinstance(codings, list):
        new_codings = []
        for i, c in enumerate(codings):
            sc, a = _sanitize_coding(c, policy, patient_ctx, release_key, f"{target}.coding[{i}]")
            actions.extend(a)
            if sc is not None:
                new_codings.append(sc)
        if new_codings:
            out["coding"] = new_codings
    text = cc.get("text")
    if isinstance(text, str) and text.strip():
        new_text, a = _mask_or_retain_text(text, policy, patient_ctx, release_key, f"{target}.text")
        out["text"] = new_text
        actions.extend(a)
    return (out or None), actions


_DOSAGE_PASSTHROUGH_SUBFIELDS = ("sequence", "timing", "asNeededBoolean", "doseAndRate")


def _sanitize_dosage(
    dosage: Any, policy: PolicyConfig, patient_ctx: PatientContext, release_key: bytes, target: str,
) -> tuple[dict | None, list[Action]]:
    """A `Dosage` (dosageInstruction[] entry) -- `text` is clinician-authored
    free text (e.g. "Ramipril 5 mg twice daily", retained if clean); `route`
    is itself a CodeableConcept and gets the same recursive scan; the rest
    (timing/doseAndRate/sequence) is structured, no embedded narrative."""
    if not isinstance(dosage, dict):
        return None, []
    out = {k: dosage[k] for k in _DOSAGE_PASSTHROUGH_SUBFIELDS if k in dosage}
    actions: list[Action] = []
    if "route" in dosage:
        new_route, a = _sanitize_codeable_concept(dosage["route"], policy, patient_ctx, release_key, f"{target}.route")
        actions.extend(a)
        if new_route is not None:
            out["route"] = new_route
    text = dosage.get("text")
    if isinstance(text, str) and text.strip():
        new_text, a = _mask_or_retain_text(text, policy, patient_ctx, release_key, f"{target}.text")
        out["text"] = new_text
        actions.extend(a)
    return (out or None), actions


def _rewrite_name_array(
    names: list, role: FieldRole, policy: PolicyConfig, patient_ctx: PatientContext, target: str,
) -> tuple[list | None, list[Action]]:
    from .policy import MASK_SPAN, SUPPRESS
    if not names:
        return None, []
    kind = policy.role_action(role)
    if kind in (MASK_SPAN, SUPPRESS):
        return None, [Action(target=target, kind="SUPPRESS", rule_id=f"FHIR_NAME_SUPPRESSED_{role.value}")]
    return names, [Action(target=target, kind="RETAIN", rule_id="FHIR_NAME_RETAINED")]


def _rewrite_reference(resolution, identity_by_resource_no: dict[int, str]) -> dict | None:
    """`resolution` is the Phase-1 Reference evidence object for this field.
    Returns a new reference object, or None if it cannot be safely rewritten
    (unresolved in the source, or its target was itself held)."""
    if resolution is None or not resolution.resolved or resolution.target_resource_no is None:
        return None
    target_identity = identity_by_resource_no.get(resolution.target_resource_no)
    if target_identity is None:  # target itself held -- dependency closure handles this upstream
        return None
    return {"reference": f"urn:uuid:{target_identity}"}


def rewrite_resource(
    resource_no: int,
    raw: dict,
    record_key: str,
    phase1_refs: list,  # ingest.fhir_adapter.Reference objects for this resource, from parse_fhir
    identity_by_resource_no: dict[int, str],
    features: list,
    patient_ctx: PatientContext,
    policy: PolicyConfig,
    release_key: bytes,
    *,
    organizer_contract: bool = False,
) -> FhirEntryResult:
    rtype = raw.get("resourceType", "")
    identity = identity_by_resource_no[resource_no]
    target_base = f"{record_key}"

    if raw.get("contained"):
        return FhirEntryResult(resource_no, rtype, identity, None, hold_reasons=["FHIR_CONTAINED_RESOURCE_UNSUPPORTED"])
    if raw.get("modifierExtension"):
        return FhirEntryResult(resource_no, rtype, identity, None, hold_reasons=["FHIR_MODIFIER_EXTENSION_UNSUPPORTED"])

    actions: list[Action] = []
    out: dict[str, Any] = {"resourceType": rtype, "id": identity}

    # Identifiers: national schemes are never tokenized (masked outright);
    # a hospital-local scheme is tokenized consistently within the release.
    from .policy import NATIONAL_SCHEMES
    new_idents = []
    for ident in raw.get("identifier", []) or []:
        if not isinstance(ident, dict) or ident.get("value") is None:
            continue
        system = ident.get("system")
        scheme = system if system else "fhir_identifier"
        if scheme in NATIONAL_SCHEMES or (system and any(s in system.lower() for s in ("aadhaar", "abha"))):
            actions.append(Action(target=f"{target_base}.identifier", kind="MASK_SPAN", rule_id="NATIONAL_ID_MASKED"))
            continue
        token = keys.identifier_token(release_key, scheme, str(ident["value"]))
        new_idents.append({"system": "urn:nijsetu:tokenized-identifier", "value": token})
        actions.append(Action(target=f"{target_base}.identifier", kind="TOKENIZE", rule_id="IDENTIFIER_TOKENIZED", replacement_ref=token))
    if organizer_contract and rtype == "Patient":
        # The organizer's records.jsonl consistency check re-opens the
        # published Bundle and requires the claimed pseudonym to actually
        # appear inside the record it is claimed for -- for a FHIR document
        # that record is the whole Bundle (locator "doc"), so the token is
        # carried on the Patient resource's own identifier array, never on
        # unrelated resources, and never in place of (always alongside) a
        # genuine tokenized local identifier.
        new_idents.append({"system": "urn:nijsetu:patient-pseudonym", "value": patient_ctx.patient_token})
        actions.append(Action(target=f"{target_base}.identifier", kind="TOKENIZE", rule_id="FHIR_PATIENT_PSEUDONYM_EMBEDDED"))
    if new_idents:
        out["identifier"] = new_idents

    name_role = _NAME_ROLE_BY_RESOURCE.get(rtype)
    if name_role is not None and raw.get("name"):
        new_names, name_actions = _rewrite_name_array(raw["name"], name_role, policy, patient_ctx, f"{target_base}.name")
        actions.extend(name_actions)
        if new_names:
            out["name"] = new_names

    if rtype == "Patient" and raw.get("gender"):
        out["gender"] = raw["gender"]  # not independently identifying; clinically useful

    if raw.get("birthDate"):
        actions.append(Action(target=f"{target_base}.birthDate", kind="SUPPRESS", rule_id="FHIR_BIRTHDATE_SUPPRESSED"))
        # deliberately never written: an age band is not a valid FHIR `date`.

    # telecom / address: direct contact always suppressed; fine address lines
    # suppressed; coarse geography (district) retained only if policy allows.
    if raw.get("telecom"):
        actions.append(Action(target=f"{target_base}.telecom", kind="SUPPRESS", rule_id="FHIR_TELECOM_SUPPRESSED"))
    new_addresses = []
    for addr in raw.get("address", []) or []:
        if not isinstance(addr, dict):
            continue
        new_addr = {}
        if addr.get("line"):
            actions.append(Action(target=f"{target_base}.address.line", kind="SUPPRESS", rule_id="FHIR_FINE_ADDRESS_SUPPRESSED"))
        if addr.get("postalCode"):
            actions.append(Action(target=f"{target_base}.address.postalCode", kind="SUPPRESS", rule_id="FHIR_PIN_SUPPRESSED"))
        if addr.get("district") and policy.geography_level != "none" and not patient_ctx.suppress_geography:
            new_addr["district"] = addr["district"]
            actions.append(Action(target=f"{target_base}.address.district", kind="RETAIN", rule_id="FHIR_DISTRICT_RETAINED"))
        elif addr.get("district"):
            actions.append(Action(target=f"{target_base}.address.district", kind="SUPPRESS", rule_id="FHIR_DISTRICT_SUPPRESSED"))
        if new_addr:
            new_addresses.append(new_addr)
    if new_addresses:
        out["address"] = new_addresses

    # Dates: period / effective* / issued / authoredOn / performedDateTime --
    # shifted by the resource's own (post-subject-merge) patient context.
    period = raw.get("period") if isinstance(raw.get("period"), dict) else None
    if period:
        new_period = {}
        for edge in ("start", "end"):
            if period.get(edge):
                shifted, held = _shift_or_hold(period[edge], patient_ctx, f"{target_base}.period.{edge}", actions)
                if shifted is not None:
                    new_period[edge] = shifted
        if new_period:
            out["period"] = new_period
    for dkey in _DATE_SCALAR_FIELDS:
        if raw.get(dkey):
            shifted, _ = _shift_or_hold(raw[dkey], patient_ctx, f"{target_base}.{dkey}", actions)
            if shifted is not None:
                out[dkey] = shifted

    for key in _SCALAR_PASSTHROUGH_FIELDS:
        if key in raw:
            out[key] = raw[key]  # lossless: same Decimal/dict/list objects as parsed; no embedded narrative
    for key in _CODING_TYPED_FIELDS:
        if key in raw:
            new_coding, scan_actions = _sanitize_coding(raw[key], policy, patient_ctx, release_key, f"{target_base}.{key}")
            actions.extend(scan_actions)
            if new_coding is not None:
                out[key] = new_coding
    for key in _CODEABLE_CONCEPT_TYPED_FIELDS:
        if key in raw:
            new_cc, scan_actions = _sanitize_codeable_concept(raw[key], policy, patient_ctx, release_key, f"{target_base}.{key}")
            actions.extend(scan_actions)
            if new_cc is not None:
                out[key] = new_cc
    if isinstance(raw.get("dosageInstruction"), list):
        new_doses = []
        for i, d in enumerate(raw["dosageInstruction"]):
            new_dose, scan_actions = _sanitize_dosage(d, policy, patient_ctx, release_key, f"{target_base}.dosageInstruction[{i}]")
            actions.extend(scan_actions)
            if new_dose is not None:
                new_doses.append(new_dose)
        if new_doses:
            out["dosageInstruction"] = new_doses
    for key in _CLINICAL_TEXT_SCAN_FIELDS:
        if isinstance(raw.get(key), str):
            new_text, scan_actions = _mask_or_retain_text(raw[key], policy, patient_ctx, release_key, f"{target_base}.{key}")
            out[key] = new_text
            actions.extend(scan_actions)

    # References: subject/encounter are required (dependency closure applies
    # upstream); performer is optional and simply omitted if unresolved.
    refs_by_key: dict[str, list] = {}
    for r in phase1_refs:
        refs_by_key.setdefault(r.field_key, []).append(r)
    unresolved_required = False
    for field_key in ("subject", "encounter"):
        for r in refs_by_key.get(field_key, []):
            new_ref = _rewrite_reference(r, identity_by_resource_no)
            if new_ref is not None:
                out[field_key] = new_ref
                actions.append(Action(target=f"{target_base}.{field_key}", kind="TOKENIZE", rule_id="FHIR_REFERENCE_REWRITTEN"))
            else:
                unresolved_required = True
    for r in refs_by_key.get("performer", []):
        new_ref = _rewrite_reference(r, identity_by_resource_no)
        if new_ref is not None:
            out["performer"] = [new_ref]
            actions.append(Action(target=f"{target_base}.performer", kind="TOKENIZE", rule_id="FHIR_REFERENCE_REWRITTEN"))

    if unresolved_required:
        return FhirEntryResult(resource_no, rtype, identity, None, actions=actions, hold_reasons=["FHIR_REQUIRED_REFERENCE_UNRESOLVED"])

    # Independent clinical-invariant check: re-derive assertions from the
    # sanitized resource we just built (not this function's own bookkeeping)
    # using the same schema-aware extractor the ingest adapter uses -- same
    # principle as the CSV/JSON/TXT reparse-based check, so a FHIR
    # admission/discharge Encounter.period interval is actually verified to
    # still hold the required duration/order after the shift, not merely
    # assumed correct because the code that wrote it looks right.
    staged_fields = _extract_resource(out, resource_no, "", None).fields
    invariant_problems = compare_assertions(extract_assertions(features), extract_assertions(staged_fields), patient_ctx.date_shift_days)
    if invariant_problems:
        return FhirEntryResult(resource_no, rtype, identity, None, actions=actions, hold_reasons=invariant_problems)

    return FhirEntryResult(resource_no, rtype, identity, out, actions=actions)


@dataclass
class FhirDocumentResult:
    tree: dict | None  # final sanitized Bundle/resource, or None if nothing survived
    actions: list[Action] = field(default_factory=list)
    held: dict[int, list[str]] = field(default_factory=dict)  # resource_no -> reasons
    released: set[int] = field(default_factory=set)  # resource_no actually present in `tree`
    released_resources: dict[int, dict] = field(default_factory=dict)  # surviving output, keyed by original resource_no


def sanitize_fhir_document(
    tree: dict,
    file_no: int,
    parse_result,  # ingest.fhir_adapter.FhirResult
    record_features: dict[str, list],
    link_decisions: dict,
    patient_contexts: dict[str, PatientContext],
    release_key: bytes,
    policy: PolicyConfig,
    held_clusters: frozenset[str] = frozenset(),
    *,
    organizer_contract: bool = False,
) -> FhirDocumentResult:
    """held_clusters: cluster_ids the risk escalation ladder already decided
    to hold BEFORE any transform runs (sanitize.py's CSV/JSON/TXT branches
    apply this same pre-filter; FHIR resources must be excluded the same
    way, not written and then reported as held, and the dependency-closure
    loop below must see them as held from the start so anything that
    required-references one of them is held too)."""
    top_rtype, raw_by_no = iter_bundle_resources(tree)
    phase1_by_no = {r.resource_no: r for r in parse_result.resources}

    held: dict[int, list[str]] = {}
    for resource_no, raw in raw_by_no:
        reasons = []
        if raw.get("contained"):
            reasons.append("FHIR_CONTAINED_RESOURCE_UNSUPPORTED")
        if raw.get("modifierExtension"):
            reasons.append("FHIR_MODIFIER_EXTENSION_UNSUPPORTED")
        record_key = f"{file_no}:{resource_no}"
        decision = link_decisions.get(record_key)
        cluster_id = decision.cluster_id if decision else record_key
        if cluster_id in held_clusters:
            reasons.append("SMALL_COHORT_RESIDUAL_RISK")
        if reasons:
            held[resource_no] = reasons

    # Fixed-point rewrite: a resource can become held for reasons only
    # discoverable during its own rewrite (e.g. an unresolved required
    # reference), after identity_by_resource_no was already handed to
    # resources earlier in iteration order. Rerunning the whole pass with
    # the updated `held` set (and therefore a shrunk identity map) until no
    # new hold appears guarantees every reference to a held resource is
    # re-evaluated and dropped, instead of being published as a dangling
    # urn:uuid: pointing at a resource missing from the published Bundle.
    actions: list[Action] = []
    sanitized_by_no: dict[int, dict] = {}
    identity_by_resource_no: dict[int, str] = {}
    changed = True
    while changed:
        changed = False
        identity_by_resource_no = {
            resource_no: keys.resource_uuid(release_key, f"{file_no}:{resource_no}")
            for resource_no, _raw in raw_by_no if resource_no not in held
        }
        actions = []
        sanitized_by_no = {}
        for resource_no, raw in raw_by_no:
            if resource_no in held:
                continue
            record_key = f"{file_no}:{resource_no}"
            decision = link_decisions.get(record_key)
            cluster_id = decision.cluster_id if decision else record_key
            ctx = patient_contexts[cluster_id]
            feats = record_features.get(record_key, [])
            res = phase1_by_no.get(resource_no)
            refs = res.references if res else []
            result = rewrite_resource(
                resource_no, raw, record_key, refs, identity_by_resource_no, feats, ctx, policy, release_key,
                organizer_contract=organizer_contract,
            )
            actions.extend(result.actions)
            if result.sanitized is None:
                held[resource_no] = result.hold_reasons
                changed = True
            else:
                sanitized_by_no[resource_no] = result.sanitized

    if top_rtype == "Bundle":
        if not sanitized_by_no:
            return FhirDocumentResult(tree=None, actions=actions, held=held)
        out_tree = {
            "resourceType": "Bundle",
            "type": tree.get("type", "collection"),
            "entry": [
                {"fullUrl": f"urn:uuid:{identity_by_resource_no[no]}", "resource": sanitized_by_no[no]}
                for no in sorted(sanitized_by_no)
            ],
        }
        return FhirDocumentResult(
            tree=out_tree, actions=actions, held=held, released=set(sanitized_by_no), released_resources=sanitized_by_no,
        )

    released = {0} if 0 in sanitized_by_no else set()
    return FhirDocumentResult(
        tree=sanitized_by_no.get(0), actions=actions, held=held, released=released, released_resources=sanitized_by_no,
    )


def _shift_or_hold(raw_text: str, patient_ctx: PatientContext, target: str, actions: list[Action]) -> tuple[str | None, bool]:
    try:
        parsed = parse_flexible_date(raw_text)
    except DateParseError as exc:
        actions.append(Action(target=target, kind="SUPPRESS", rule_id=f"DATE_HELD_{exc.reason_code}"))
        return None, True
    shifted = shift_date_text(parsed, patient_ctx.date_shift_days)
    actions.append(Action(target=target, kind="SHIFT_DATE", rule_id="DATE_SHIFTED"))
    return shifted, False
