"""Adapter-level tests: CSV/TSV, JSON, TXT, FHIR, encodings, limits, malformed inputs."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from nijsetu.config import Limits
from nijsetu.detect import detect_in_text
from nijsetu.errors import LimitExceededError, MalformedInputError, UnsupportedFormatError
from nijsetu.ingest.csv_adapter import parse_csv
from nijsetu.ingest.encoding import detect_and_decode
from nijsetu.ingest.fhir_adapter import parse_fhir
from nijsetu.ingest.json_adapter import parse_json
from nijsetu.ingest.registry import discover_files
from nijsetu.ingest.txt_adapter import parse_txt
from nijsetu.schema import Disposition, FieldRole, ValueType


# --- encoding ----------------------------------------------------------------

def test_strict_utf8_decodes():
    text, enc = detect_and_decode("Rāmēsh".encode("utf-8"))
    assert text == "Rāmēsh"
    assert enc == "utf-8"


def test_utf8_bom_stripped():
    text, enc = detect_and_decode(b"\xef\xbb\xbfhello")
    assert text == "hello"
    assert enc == "utf-8-sig"


def test_utf16_bom_decodes():
    raw = "hello".encode("utf-16")
    text, enc = detect_and_decode(raw)
    assert text == "hello"
    assert enc == "utf-16"


def test_undecodable_raises():
    with pytest.raises(UnsupportedFormatError):
        detect_and_decode(b"\xff\xfe\x00\xff\xff\xfe\xfe")


# --- CSV / TSV -----------------------------------------------------------------

def test_csv_preserves_column_order_and_leading_zeros():
    raw = b"Patient Name,UHID\nRamesh Kumar,007\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert result.file_record.record_count == 1
    row = result.rows[0]
    assert [f.source.path for f in row] == ["Patient Name", "UHID"]
    assert row[1].value == "007"  # leading zero preserved as text, not int


def test_csv_quoted_multiline_cell():
    raw = b'Patient Name,Notes\n"Kiran Shah","Line one.\nLine two."\n'
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert result.rows[0][1].original_text == "Line one.\nLine two."


def test_csv_malformed_row_field_count_held():
    raw = b"Patient Name,UHID,Phone\nA,1,2,EXTRA\nB,2\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert len(result.rows) == 0
    assert len(result.held_rows) == 2
    assert all("MALFORMED_ROW_FIELD_COUNT" in reasons for _, reasons in result.held_rows)


def test_csv_duplicate_header_holds_whole_file():
    raw = b"Patient Name,Patient Name\nA,B\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert result.file_record.disposition == Disposition.HELD
    assert "DUPLICATE_HEADER" in result.file_record.reason_codes


def test_csv_ambiguous_alias_headers_are_reported_but_keep_their_role():
    """Two columns claiming one canonical field is a genuine ambiguity about
    WHICH column is the real one -- and it is reported as such -- but it is
    not an ambiguity about whether the values are personal data.

    This previously demoted both columns to OTHER, which resolves the
    ambiguity in the only unsafe direction: OTHER means RETAIN, so the
    values were published verbatim. The role must survive so the privacy
    action still applies; only the field's identity meaning is unresolved.
    """
    from nijsetu.schema import FieldRole

    raw = b"Patient Name,Pt Name\nA,B\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert "AMBIGUOUS_HEADER_MAPPING" in result.file_record.reason_codes
    assert all(f.role == FieldRole.PATIENT_NAME for f in result.rows[0]), (
        "an ambiguous mapping must not strip the PII role, or the values get retained in clear"
    )


def test_colliding_identifier_columns_do_not_retain_identifiers_in_clear():
    """Regression for a measured 230-identifier leak.

    A lab export legitimately carries both a specimen number and a patient
    number. When both headers resolved to the same canonical, the ambiguity
    guard demoted them to OTHER and every MRN in four lab CSVs was published
    verbatim. Specimen- and patient-level keys now hold distinct canonicals,
    so no collision occurs at all -- and even if one did, the role survives.
    """
    from nijsetu.schema import FieldRole

    raw = b"sample_id,patient_name,uhid,test,result\nS26000199,Geeta Chauhan,JJH-0077132,Haemoglobin,11.0\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    assert "AMBIGUOUS_HEADER_MAPPING" not in result.file_record.reason_codes
    by_header = {f.source.path: f.role for f in result.rows[0]}
    assert by_header["sample_id"] == FieldRole.PATIENT_IDENTIFIER
    assert by_header["uhid"] == FieldRole.PATIENT_IDENTIFIER
    assert by_header["patient_name"] == FieldRole.PATIENT_NAME
    # Clinical columns must stay untouched.
    assert by_header["test"] == FieldRole.OTHER
    assert by_header["result"] == FieldRole.OTHER


def test_event_level_identifier_schemes_are_not_identity_anchors():
    """A specimen/episode/bill number identifies an EVENT. One patient owns
    many, so treating them as patient-level anchors turns ordinary repeat
    activity into an identifier contradiction and vetoes genuine merges.
    They must still be tokenized (they are patient-linkable), just never
    used as identity anchors."""
    from nijsetu.ingest.header_aliases import EVENT_SCOPED_SCHEMES, resolve_header

    for header in ("sample_id", "accession no", "IPD No", "case no", "bill no"):
        canonical, _role = resolve_header(header)
        assert canonical in EVENT_SCOPED_SCHEMES, f"{header!r} -> {canonical!r} should be event-scoped"
    for header in ("uhid", "mrn", "patient id", "aadhaar", "abha number"):
        canonical, _role = resolve_header(header)
        assert canonical not in EVENT_SCOPED_SCHEMES, f"{header!r} -> {canonical!r} must stay a patient anchor"


def test_tsv_dialect():
    raw = b"Patient Name\tUHID\nRamesh\tUH1\n"
    result = parse_csv(raw, "r.tsv", 0, "tsv")
    assert result.rows[0][0].original_text == "Ramesh"


def test_csv_camelcase_header_resolves_to_patient_name_role():
    raw = b"patientName,UHID\nZevqori Alqaven,UH1\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    name_field = next(f for f in result.rows[0] if f.source.path == "patientName")
    assert name_field.role == FieldRole.PATIENT_NAME


def test_csv_empty_file():
    result = parse_csv(b"", "r.csv", 0, "csv")
    assert result.file_record.record_count == 0


def test_csv_bare_trailing_date_format_hint_header_resolves_to_dob():
    # "DOB (MM/DD/YYYY)" already resolves via the parenthetical-stripping
    # pass; an unseen hospital spelling the same hint without parentheses
    # ("DOB_YYYYMMDD") must resolve identically, not fall through to OTHER
    # (= RETAIN, i.e. the literal birth date published verbatim).
    raw = b"UHID,DOB_YYYYMMDD\nUH1,19901203\n"
    result = parse_csv(raw, "r.csv", 0, "csv")
    dob_field = next(f for f in result.rows[0] if f.source.path == "DOB_YYYYMMDD")
    assert dob_field.role == FieldRole.DATE


# --- JSON ----------------------------------------------------------------------

def test_json_object_record():
    raw = b'{"patientName": "Neha", "uhid": "UH1"}'
    result = parse_json(raw, "r.json", 0, Limits())
    assert len(result.records) == 1
    paths = {f.source.path for f in result.records[0]}
    assert "patientName" in paths


def test_json_array_records():
    raw = b'[{"a": 1}, {"a": 2}]'
    result = parse_json(raw, "r.json", 0, Limits())
    assert len(result.records) == 2


def test_json_record_wrapper_key():
    raw = b'{"meta": {}, "records": [{"a": 1}]}'
    result = parse_json(raw, "r.json", 0, Limits(), record_wrapper_key="records")
    assert len(result.records) == 1


def test_json_wrapper_key_missing_raises():
    raw = b'{"meta": {}}'
    with pytest.raises(MalformedInputError):
        parse_json(raw, "r.json", 0, Limits(), record_wrapper_key="records")


def test_json_duplicate_key_rejected():
    raw = b'{"a": 1, "a": 2}'
    with pytest.raises(MalformedInputError):
        parse_json(raw, "r.json", 0, Limits())


def test_json_syntax_error():
    with pytest.raises(MalformedInputError):
        parse_json(b'{"a": 1,}', "r.json", 0, Limits())


def test_json_size_limit():
    limits = Limits(max_json_bytes=10)
    with pytest.raises(LimitExceededError):
        parse_json(b'{"a": "this is too large"}', "r.json", 0, limits)


def test_json_depth_limit():
    nested = "[" * 100 + "1" + "]" * 100
    limits = Limits(max_json_depth=10)
    with pytest.raises(LimitExceededError):
        parse_json(nested.encode(), "r.json", 0, limits)


def test_json_precision_preserved_for_big_int_and_decimal():
    raw = b'{"bigId": 123456789012345678901234567890, "hba1c": 8.2}'
    result = parse_json(raw, "r.json", 0, Limits())
    fields = {f.source.path: f for f in result.records[0]}
    assert fields["bigId"].value == 123456789012345678901234567890
    assert fields["bigId"].value_type == ValueType.INTEGER
    assert fields["hba1c"].value == Decimal("8.2")  # not a float, no binary rounding


def test_json_inspects_keys_and_values():
    raw = b'{"Arif": "value"}'
    result = parse_json(raw, "r.json", 0, Limits())
    key_fields = [f for f in result.records[0] if f.source.path.startswith("<key:")]
    assert key_fields and key_fields[0].value == "Arif"


# --- camelCase/PascalCase header normalization ---------------------------

def test_camelcase_json_key_resolves_to_patient_name_role():
    raw = b'{"patientName": "Zevqori Alqaven"}'
    result = parse_json(raw, "r.json", 0, Limits())
    value_field = next(f for f in result.records[0] if f.source.path == "patientName")
    assert value_field.role == FieldRole.PATIENT_NAME


def test_pascalcase_json_key_resolves_to_patient_name_role():
    raw = b'{"PatientName": "Zevqori Alqaven"}'
    result = parse_json(raw, "r.json", 0, Limits())
    value_field = next(f for f in result.records[0] if f.source.path == "PatientName")
    assert value_field.role == FieldRole.PATIENT_NAME


def test_camelcase_key_text_itself_is_not_tagged_with_its_own_schema_role():
    """A key literally named "patientName" is structural metadata, not a
    patient name -- only the VALUE field gets the PATIENT_NAME role. The
    key is still inspected (role OTHER, picked up by free-text detection
    only if its own text happens to look like a name)."""
    raw = b'{"patientName": "Zevqori Alqaven"}'
    result = parse_json(raw, "r.json", 0, Limits())
    key_field = next(f for f in result.records[0] if f.source.path == "<key:patientName>")
    assert key_field.role == FieldRole.OTHER


def test_unfamiliar_camelcase_name_is_detected_via_structured_route_not_dropped():
    """Direct reproduction: before camelCase normalization, a name absent
    from the gazetteer and with no honorific/relation cue (as any real
    unseen patient name would be) was silently undetected entirely because
    the field never reached the structured whole-field span -- it fell
    through to best-effort free-text heuristics, which found nothing."""
    raw = b'{"patientName": "Zevqori Alqaven"}'
    result = parse_json(raw, "r.json", 0, Limits())
    value_field = next(f for f in result.records[0] if f.source.path == "patientName")
    feats = detect_in_text(value_field.original_text, value_field.source, structured_role=value_field.role)
    assert len(feats) == 1
    feat = feats[0]
    assert feat.role == FieldRole.PATIENT_NAME
    assert feat.original_text == "Zevqori Alqaven"
    assert feat.confidence_tier == "HIGH"
    # original source coordinates: the whole field value, from its own start
    assert feat.char_start == 0
    assert feat.char_end == len("Zevqori Alqaven")


def test_unfamiliar_camelcase_name_detected_in_a_nested_field():
    raw = b'{"visit": {"doctorName": "Xandri Ovelkash"}}'
    result = parse_json(raw, "r.json", 0, Limits())
    value_field = next(f for f in result.records[0] if f.source.path == "visit.doctorName")
    assert value_field.role == FieldRole.PROVIDER_NAME
    feats = detect_in_text(value_field.original_text, value_field.source, structured_role=value_field.role)
    assert len(feats) == 1
    assert feats[0].role == FieldRole.PROVIDER_NAME
    assert feats[0].original_text == "Xandri Ovelkash"
    assert feats[0].char_start == 0 and feats[0].char_end == len("Xandri Ovelkash")


def test_camelcase_fix_does_not_pollute_identity_name_tokens(tmp_path: Path):
    """End-to-end: an unfamiliar name reached only through a camelCase JSON
    key must appear in the private artifact as a clean, single PATIENT_NAME
    feature spanning the whole value -- no spurious extra feature for the
    key text itself."""
    from nijsetu.config import AppConfig
    from nijsetu.plan import run_pipeline

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "r.json").write_text('{"patientName": "Zevqori Alqaven"}', encoding="utf-8")
    plan = run_pipeline(in_dir, AppConfig())
    record = plan.private["records"][0]
    name_feats = [f for f in record["features"] if f["role"] == "PATIENT_NAME"]
    assert len(name_feats) == 1
    assert name_feats[0]["original_text"] == "Zevqori Alqaven"


# --- TXT -------------------------------------------------------------------

def test_txt_line_and_section_tracking():
    raw = "Address: C/O Ramesh\nOPD: C/O fever\n".encode()
    result = parse_txt(raw, "r.txt", 0)
    doc = result.document
    opd_offset = result.document.text.index("OPD")
    assert doc.section_at(0) == "ADDRESS"
    assert doc.section_at(opd_offset) == "OPD"
    assert doc.line_no(opd_offset) == 1


def test_txt_empty_file_zero_records():
    result = parse_txt(b"", "r.txt", 0)
    assert result.file_record.record_count == 0


# --- FHIR --------------------------------------------------------------------

def test_fhir_patient_resource_extracts_identifier_and_name():
    raw = b'''{
        "resourceType": "Patient", "id": "p1",
        "identifier": [{"system": "https://hospitalA.example/uhid", "value": "UH1"}],
        "name": [{"text": "Farhan Qureshi"}],
        "birthDate": "1990-05-12"
    }'''
    result = parse_fhir(raw, "r.json", Limits())
    assert len(result.resources) == 1
    res = result.resources[0]
    assert res.scoped_identifiers[0].issuer == "https://hospitalA.example/uhid"
    assert any(f.original_text == "Farhan Qureshi" for f in res.fields)


def test_fhir_bundle_reference_resolution():
    raw = b'''{
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:abc", "resource": {"resourceType": "Patient", "id": "p1"}},
            {"resource": {"resourceType": "Encounter", "id": "e1",
                "subject": {"reference": "urn:uuid:abc"}}},
            {"resource": {"resourceType": "Observation", "id": "o1",
                "subject": {"reference": "Patient/does-not-exist"}}}
        ]
    }'''
    result = parse_fhir(raw, "r.json", Limits())
    encounter = next(r for r in result.resources if r.resource_type == "Encounter")
    observation = next(r for r in result.resources if r.resource_type == "Observation")
    assert encounter.references[0].resolved is True
    assert observation.references[0].resolved is False


def test_fhir_missing_resource_type_rejected():
    with pytest.raises(MalformedInputError):
        parse_fhir(b'{"id": "x"}', "r.json", Limits())


def test_fhir_unsupported_resource_type_rejected():
    with pytest.raises(MalformedInputError):
        parse_fhir(b'{"resourceType": "Medication", "id": "x"}', "r.json", Limits())


def test_fhir_identifier_without_system_still_gets_a_scheme():
    """Finding 2: an identifier.value with no system must not be silently
    dropped from scoped identity evidence -- it still needs *some* scheme so
    the normal conservative fallback-issuer logic applies (see
    identity.build_candidate_features), it just isn't nationally/explicitly
    scoped."""
    raw = b'{"resourceType": "Patient", "id": "p1", "identifier": [{"value": "UH1"}]}'
    result = parse_fhir(raw, "r.json", Limits())
    ident_field = next(f for f in result.resources[0].fields if f.role.value == "PATIENT_IDENTIFIER")
    assert ident_field.issuer is None
    assert ident_field.scheme == "fhir_identifier"


def test_fhir_identifier_with_system_carries_issuer_onto_the_field():
    raw = b'''{"resourceType": "Patient", "id": "p1",
        "identifier": [{"system": "https://hospitalA.example/uhid", "value": "UH1"}]}'''
    result = parse_fhir(raw, "r.json", Limits())
    ident_field = next(f for f in result.resources[0].fields if f.role.value == "PATIENT_IDENTIFIER")
    assert ident_field.issuer == "https://hospitalA.example/uhid"
    assert ident_field.scheme == "https://hospitalA.example/uhid"


# --- FHIR: subject reference -> patient association (Finding 2) -------------

def _bundle(entries):
    return json.dumps({"resourceType": "Bundle", "type": "collection", "entry": entries}).encode()


def test_fhir_subject_reference_establishes_patient_association():
    raw = _bundle([
        {"resource": {"resourceType": "Patient", "id": "p1"}},
        {"resource": {"resourceType": "Encounter", "id": "e1", "subject": {"reference": "Patient/p1"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    encounter = next(r for r in result.resources if r.resource_type == "Encounter")
    assert encounter.patient_subject_resource_no == 0
    assert encounter.patient_subject_ambiguous is False


def test_fhir_performer_reference_never_establishes_patient_association():
    raw = _bundle([
        {"resource": {"resourceType": "Practitioner", "id": "doc1"}},
        {"resource": {"resourceType": "Observation", "id": "o1", "performer": {"reference": "Practitioner/doc1"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    observation = next(r for r in result.resources if r.resource_type == "Observation")
    assert observation.patient_subject_resource_no is None
    assert observation.patient_subject_ambiguous is False


def test_fhir_subject_reference_to_non_patient_is_not_a_patient_association():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "e1"}},
        {"resource": {"resourceType": "Observation", "id": "o1", "subject": {"reference": "Encounter/e1"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    observation = next(r for r in result.resources if r.resource_type == "Observation")
    assert observation.patient_subject_resource_no is None


def test_fhir_ambiguous_subject_reference_is_held_not_arbitrarily_resolved():
    raw = _bundle([
        {"resource": {"resourceType": "Patient", "id": "pX"}},
        {"resource": {"resourceType": "Patient", "id": "pY"}},
        {"resource": {"resourceType": "Observation", "id": "o1",
                      "subject": [{"reference": "Patient/pX"}, {"reference": "Patient/pY"}]}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    observation = next(r for r in result.resources if r.resource_type == "Observation")
    assert observation.patient_subject_resource_no is None
    assert observation.patient_subject_ambiguous is True


def test_fhir_duplicate_logical_id_does_not_resolve_arbitrarily():
    raw = _bundle([
        {"resource": {"resourceType": "Patient", "id": "dup"}},
        {"resource": {"resourceType": "Patient", "id": "dup"}},
        {"resource": {"resourceType": "Encounter", "id": "e1", "subject": {"reference": "Patient/dup"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    encounter = next(r for r in result.resources if r.resource_type == "Encounter")
    ref = encounter.references[0]
    assert ref.resolved is False
    assert encounter.patient_subject_resource_no is None


def test_fhir_two_distinct_patients_each_keep_their_own_clinical_records():
    raw = _bundle([
        {"resource": {"resourceType": "Patient", "id": "pA"}},
        {"resource": {"resourceType": "Encounter", "id": "eA", "subject": {"reference": "Patient/pA"}}},
        {"resource": {"resourceType": "Patient", "id": "pB"}},
        {"resource": {"resourceType": "Observation", "id": "oB", "subject": {"reference": "Patient/pB"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    by_id = {r.logical_id: r for r in result.resources}
    assert by_id["eA"].patient_subject_resource_no == by_id["pA"].resource_no
    assert by_id["oB"].patient_subject_resource_no == by_id["pB"].resource_no
    assert by_id["eA"].patient_subject_resource_no != by_id["oB"].patient_subject_resource_no


# --- FHIR: owner-scoped contained references (Finding 3) --------------------

def test_fhir_contained_reference_resolves_within_its_own_owner():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "encA",
                      "contained": [{"resourceType": "Patient", "id": "elsewhere"}],
                      "subject": {"reference": "#elsewhere"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    assert result.resources[0].references[0].resolved is True


def test_fhir_contained_reference_does_not_resolve_against_a_different_owner():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "encB",
                      "contained": [{"resourceType": "Patient", "id": "elsewhere"}]}},
        {"resource": {"resourceType": "Observation", "id": "obsOther", "subject": {"reference": "#elsewhere"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    observation = next(r for r in result.resources if r.resource_type == "Observation")
    assert observation.references[0].resolved is False


def test_fhir_reused_contained_id_resolves_independently_per_owner():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "encA",
                      "contained": [{"resourceType": "Patient", "id": "elsewhere"}],
                      "subject": {"reference": "#elsewhere"}}},
        {"resource": {"resourceType": "Encounter", "id": "encB",
                      "contained": [{"resourceType": "Patient", "id": "elsewhere"}],
                      "subject": {"reference": "#elsewhere"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    assert all(r.references[0].resolved is True for r in result.resources)


def test_fhir_duplicate_contained_id_within_one_owner_does_not_resolve():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "encDup",
                      "contained": [{"resourceType": "Patient", "id": "dup"}, {"resourceType": "Patient", "id": "dup"}],
                      "subject": {"reference": "#dup"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    assert result.resources[0].references[0].resolved is False


def test_fhir_missing_contained_target_does_not_resolve():
    raw = _bundle([
        {"resource": {"resourceType": "Encounter", "id": "encMissing", "subject": {"reference": "#missing"}}},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    assert result.resources[0].references[0].resolved is False


# --- FHIR: unsupported/malformed Bundle entries are never silent (Finding 4) -

def test_fhir_bundle_with_only_unsupported_entries_is_held_not_clean():
    raw = _bundle([{"resource": {"resourceType": "Organization", "id": "org1"}}])
    result = parse_fhir(raw, "r.json", Limits())
    assert result.resources == []
    assert result.file_record.disposition == Disposition.HELD
    assert "FHIR_NO_SUPPORTED_RESOURCES_IN_BUNDLE" in result.file_record.reason_codes
    assert result.file_record.record_count == 0


def test_fhir_bundle_mixed_entries_hold_unsupported_and_malformed_explicitly():
    raw = _bundle([
        {"resource": {"resourceType": "Patient", "id": "p1"}},
        {"resource": {"resourceType": "Organization", "id": "org1"}},
        {"resource": {"id": "noType1"}},
        "not-a-dict-entry",
        {},
    ])
    result = parse_fhir(raw, "r.json", Limits())
    assert len(result.resources) == 1
    assert result.file_record.disposition == Disposition.PROCESSED
    assert "FHIR_ENTRIES_HELD" in result.file_record.reason_codes
    reasons_by_ordinal = dict(result.held_resources)
    assert reasons_by_ordinal[1] == ["FHIR_RESOURCE_TYPE_UNSUPPORTED_IN_BUNDLE"]
    assert reasons_by_ordinal[2] == ["FHIR_ENTRY_MISSING_RESOURCE"]
    assert reasons_by_ordinal[3] == ["FHIR_ENTRY_MALFORMED"]
    assert reasons_by_ordinal[4] == ["FHIR_ENTRY_MISSING_RESOURCE"]


def test_fhir_bundle_genuinely_empty_entry_list_is_clean_processed():
    raw = _bundle([])
    result = parse_fhir(raw, "r.json", Limits())
    assert result.resources == []
    assert result.held_resources == []
    assert result.file_record.disposition == Disposition.PROCESSED
    assert result.file_record.reason_codes == []


# --- registry / root boundary --------------------------------------------------

def test_discover_files_skips_symlinks(tmp_path: Path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "a.csv").write_text("a,b\n1,2\n")
    link_dir = tmp_path / "root"
    link_dir.mkdir()
    (link_dir / "a.csv").write_text("a,b\n1,2\n")
    try:
        (link_dir / "escape_link").symlink_to(real_dir)
    except OSError:
        pytest.skip("symlinks not supported in this environment")
    files = discover_files(link_dir)
    assert all("escape_link" not in f.rel_path for f in files)


def test_discover_files_unknown_format(tmp_path: Path):
    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4")
    files = discover_files(tmp_path)
    assert files[0].declared_format == "unknown"


def test_discover_empty_directory(tmp_path: Path):
    assert discover_files(tmp_path) == []
