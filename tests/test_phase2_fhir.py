"""Unit tests: FHIR whitelist-reconstruction rewriter (phase2.fhir_rewrite)."""

from __future__ import annotations

import json
import re

from nijsetu.config import Limits
from nijsetu.ingest.fhir_adapter import parse_fhir, parse_fhir_tree
from nijsetu.phase2 import keys
from nijsetu.phase2.fhir_rewrite import sanitize_fhir_document
from nijsetu.phase2.patient_context import PatientContext
from nijsetu.phase2.policy import BALANCED

RK = keys.derive_release_key(b"k" * 20, "REL1")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _bundle(extra_patient_fields=None, encounter_contained=None, encounter_modifier_ext=None):
    patient = {
        "resourceType": "Patient", "id": "pat-1",
        "identifier": [{"system": "urn:local:uhid", "value": "FH001"}],
        "name": [{"text": "Meera Sharma"}],
        "birthDate": "1988-05-20",
        "telecom": [{"system": "phone", "value": "9123456780"}],
        "address": [{"district": "Jaipur", "postalCode": "302001"}],
    }
    if extra_patient_fields:
        patient.update(extra_patient_fields)
    encounter = {
        "resourceType": "Encounter", "id": "enc-1", "status": "finished",
        "subject": {"reference": "urn:uuid:patient-ref"},
        "period": {"start": "2024-10-12", "end": "2024-10-17"},
    }
    if encounter_contained is not None:
        encounter["contained"] = encounter_contained
    if encounter_modifier_ext is not None:
        encounter["modifierExtension"] = encounter_modifier_ext
    observation = {
        "resourceType": "Observation", "id": "obs-1", "status": "final",
        "code": {"text": "Blood Pressure"},
        "subject": {"reference": "urn:uuid:patient-ref"},
        "encounter": {"reference": "urn:uuid:encounter-ref"},
        "valueQuantity": {"value": 140, "unit": "mmHg"},
    }
    return {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:patient-ref", "resource": patient},
            {"fullUrl": "urn:uuid:encounter-ref", "resource": encounter},
            {"fullUrl": "urn:uuid:obs-ref", "resource": observation},
        ],
    }


def _ctx(cluster_id="0:1"):
    return PatientContext(
        cluster_id=cluster_id, canonical_key=cluster_id, patient_token=keys.patient_token(RK, cluster_id),
        date_shift_days=5, reference_event_date=None, age_band=None, age_band_reason="NO_DOB",
    )


def _run(bundle: dict):
    raw = json.dumps(bundle).encode()
    tree = parse_fhir_tree(raw, Limits())
    parse_result = parse_fhir(raw, "x.json", Limits(), file_no=0)
    record_features = {f"0:{i}": [] for i in range(3)}
    link_decisions = {f"0:{i}": type("D", (), {"cluster_id": "0:1"})() for i in range(3)}
    patient_contexts = {"0:1": _ctx()}
    return sanitize_fhir_document(tree, 0, parse_result, record_features, link_decisions, patient_contexts, RK, BALANCED)


def test_references_rewritten_and_resolve_within_bundle():
    result = _run(_bundle())
    assert result.tree is not None
    entries = result.tree["entry"]
    ids = {e["resource"]["id"] for e in entries}
    for e in entries:
        res = e["resource"]
        for key in ("subject", "encounter"):
            if key in res:
                target = res[key]["reference"][len("urn:uuid:"):]
                assert target in ids


def test_resource_id_and_fullurl_are_valid_uuids():
    result = _run(_bundle())
    for e in result.tree["entry"]:
        assert UUID_RE.match(e["resource"]["id"])
        assert e["fullUrl"] == f"urn:uuid:{e['resource']['id']}"


def test_direct_identifiers_never_survive():
    result = _run(_bundle())
    dump = json.dumps(result.tree)
    assert "Meera Sharma" not in dump
    assert "9123456780" not in dump
    assert "302001" not in dump
    assert "FH001" not in dump  # raw UHID value replaced by a token
    assert "TKN-" in dump


def test_birthdate_never_emitted_even_as_age_band():
    result = _run(_bundle())
    dump = json.dumps(result.tree)
    assert "birthDate" not in dump
    assert "1988" not in dump


def test_coarse_geography_retained_under_balanced_policy():
    result = _run(_bundle())
    dump = json.dumps(result.tree)
    assert "Jaipur" in dump


def test_dates_shifted_consistently_and_interval_preserved():
    result = _run(_bundle())
    encounter = next(e["resource"] for e in result.tree["entry"] if e["resource"]["resourceType"] == "Encounter")
    assert encounter["period"]["start"] == "2024-10-17"  # +5 days
    assert encounter["period"]["end"] == "2024-10-22"


def test_contained_resource_holds_owning_unit_not_whole_bundle():
    result = _run(_bundle(encounter_contained=[{"resourceType": "Observation", "id": "inner"}]))
    assert 1 in result.held  # Encounter is resource_no 1
    assert "FHIR_CONTAINED_RESOURCE_UNSUPPORTED" in result.held[1]
    # Patient and Observation still publish; Observation's required `encounter`
    # reference pointed at the held Encounter, so it cascades via dependency closure.
    assert 2 in result.held
    assert 0 not in result.held


def test_modifier_extension_holds_resource():
    result = _run(_bundle(encounter_modifier_ext=[{"url": "http://example/mod", "valueBoolean": True}]))
    assert 1 in result.held
    assert "FHIR_MODIFIER_EXTENSION_UNSUPPORTED" in result.held[1]


def test_clinical_coded_fields_preserved_losslessly():
    result = _run(_bundle())
    obs = next(e["resource"] for e in result.tree["entry"] if e["resource"]["resourceType"] == "Observation")
    assert obs["valueQuantity"] == {"value": 140, "unit": "mmHg"}
    assert obs["code"] == {"text": "Blood Pressure"}
