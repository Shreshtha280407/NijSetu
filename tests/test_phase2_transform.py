"""Unit tests: CSV/JSON/TXT transformers and span overlap resolution."""

from __future__ import annotations

from nijsetu.detect import detect_in_text
from nijsetu.ingest.header_aliases import resolve_header
from nijsetu.phase2 import keys
from nijsetu.phase2.patient_context import PatientContext
from nijsetu.phase2.policy import BALANCED
from nijsetu.phase2.spans import apply_span_replacements, resolve_overlaps
from nijsetu.phase2.transform_csv import transform_csv
from nijsetu.phase2.transform_json import transform_json_record
from nijsetu.phase2.transform_txt import transform_txt
from nijsetu.schema import FieldRole, SourceCoordinate

RK = keys.derive_release_key(b"k" * 20, "REL1")


def _ctx(cluster_id="p0", shift=0):
    return PatientContext(
        cluster_id=cluster_id, canonical_key=cluster_id, patient_token=keys.patient_token(RK, cluster_id),
        date_shift_days=shift, reference_event_date=None, age_band=None, age_band_reason="NO_DOB",
    )


def _row_fields(header, values):
    out = []
    for h, v in zip(header, values):
        canonical, role = resolve_header(h)
        src = SourceCoordinate(file_no=0, file_path="x.csv", record_no=0, path=h)
        out.append(_field(src, role, v))
    return out


def _field(src, role, value):
    from nijsetu.schema import Field, ValueType
    return Field(source=src, value_type=ValueType.STRING, value=value, original_text=value, role=role)


def test_csv_preserves_header_schema_labels_and_column_order():
    header = ["Patient Name", "UHID", "Notes"]
    fields = _row_fields(header, ["Mohammad Arif", "AR001", "no issues"])
    feats = []
    for f in fields:
        feats.extend(detect_in_text(f.value, f.source, structured_role=f.role))
    result = transform_csv(header, [fields], {0: feats}, {0: _ctx()}, BALANCED, RK, ",")
    assert not result.held
    lines = result.text.splitlines()
    assert lines[0] == "Patient Name,UHID,Notes"
    assert "[REDACTED_NAME]" in lines[1]
    assert "TKN-UHID-" in lines[1]
    assert "no issues" in lines[1]  # untouched free-text column retained


def test_csv_header_collision_after_sanitization_holds_whole_file():
    header = ["A", "A"]
    fields = _row_fields(header, ["x", "y"])
    result = transform_csv(header, [fields], {0: []}, {0: _ctx()}, BALANCED, RK, ",")
    assert result.held
    assert "CSV_HEADER_COLLISION_AFTER_SANITIZATION" in result.hold_reasons


def test_json_renames_a_dynamic_key_carrying_pii_but_preserves_schema_keys():
    obj = {"patientName": "Mohammad Arif", "Mohammad Arif": {"note": "seen today"}}
    feats = []
    from nijsetu.ingest.json_adapter import _walk
    raw_fields = []
    _walk(obj, "", raw_fields)
    for fpath, vtype, fval, ftext in raw_fields:
        last = fpath.rsplit(".", 1)[-1]
        role = FieldRole.OTHER if last.startswith("<key:") else resolve_header(last.split("[")[0])[1]
        src = SourceCoordinate(file_no=0, file_path="x.json", record_no=0, path=fpath or "$")
        feats.extend(detect_in_text(ftext, src, structured_role=role))
    result = transform_json_record(obj, "0:0", feats, _ctx(), BALANCED, RK)
    assert not result.held
    assert result.value["patientName"] == "[REDACTED_NAME]"
    assert "Mohammad Arif" not in result.value
    assert any(k.startswith("SANITIZED_KEY_") for k in result.value)


def test_json_preserves_nested_types_and_precision_when_untouched():
    from decimal import Decimal
    obj = {"labs": {"hba1c": Decimal("8.2"), "count": 3, "flag": True, "note": None}, "items": [1, 2, 3]}
    result = transform_json_record(obj, "0:0", [], _ctx(), BALANCED, RK)
    assert result.value["labs"]["hba1c"] == Decimal("8.2")
    assert result.value["labs"]["count"] == 3
    assert result.value["labs"]["flag"] is True
    assert result.value["labs"]["note"] is None
    assert result.value["items"] == [1, 2, 3]


def test_txt_right_to_left_replacement_preserves_untouched_text():
    text = "Patient: Mohammad Arif, UHID AR001\nBP 140/90. Ramipril 5 mg twice daily.\n"
    base = SourceCoordinate(file_no=0, file_path="x.txt", record_no=0, path="body", char_start=0)
    feats = detect_in_text(text, base)
    result = transform_txt(text, feats, _ctx(), BALANCED, RK, "0:0")
    assert "Mohammad Arif" not in result.text
    assert "AR001" not in result.text
    assert "140/90" in result.text
    assert "Ramipril 5 mg twice daily" in result.text


def test_resolve_overlaps_merges_and_prefers_higher_priority_role():
    from nijsetu.schema import DetectorEvidence, Feature

    src = SourceCoordinate(file_no=0, file_path="x", record_no=0, path="body")
    a = Feature(src, FieldRole.PATIENT_NAME, 0, 10, "Mohammad A", evidence=[DetectorEvidence("x", "CUE", "x")])
    b = Feature(src, FieldRole.PATIENT_IDENTIFIER, 5, 15, "d AR00123", scheme="uhid", evidence=[DetectorEvidence("x", "FORMAT", "x")])
    merged = resolve_overlaps([a, b])
    assert len(merged) == 1
    assert merged[0].role == FieldRole.PATIENT_IDENTIFIER  # identifier outranks name
    assert merged[0].char_start == 0 and merged[0].char_end == 15


def test_apply_span_replacements_is_right_to_left_and_order_independent():
    text = "ABCDEFGHIJ"
    repls = [(0, 2, "xx"), (5, 7, "yy")]
    assert apply_span_replacements(text, repls) == "xxCDEyyHIJ"
    assert apply_span_replacements(text, list(reversed(repls))) == "xxCDEyyHIJ"
