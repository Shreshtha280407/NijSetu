"""Adversarial tests for record-specific pseudonym verification.

The defect these exist for: verification used to search the whole exported
FILE's text for the claimed token. That made every record in a file inherit the
strongest claim any record in it could make, so a file containing one
correctly-tokenized row would vouch for rows whose pseudonym was wrong,
missing, or belonged to a different patient.

Every test below is written so that it would PASS under a whole-file text
search and must FAIL under correct record-specific verification. A regression
to file-wide scope therefore breaks these tests rather than silently restoring
the unsound behaviour.
"""

from __future__ import annotations

import json

import pytest

from nijsetu.record_locator import (
    find_duplicate_locators,
    json_pointer_unescape,
    resolve_json_pointer,
    verify_csv_claim,
    verify_document_claim,
    verify_json_claim,
)

TOKEN_A = "NS-P-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "NS-P-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

CSV_EXPORT = (
    "uhid,patient_name,test,result,patient_ref\n"
    f"TKN-1,[REDACTED_NAME],Haemoglobin,11.0,{TOKEN_A}\n"
    f"TKN-2,[REDACTED_NAME],Haemoglobin,13.5,{TOKEN_B}\n"
)


# --------------------------------------------------------------------------
# CSV: cross-patient, missing, out-of-range and swapped claims
# --------------------------------------------------------------------------

def test_csv_claim_verifies_in_its_own_row():
    assert verify_csv_claim(CSV_EXPORT, ",", "row:0", TOKEN_A).verified
    assert verify_csv_claim(CSV_EXPORT, ",", "row:1", TOKEN_B).verified


def test_csv_cross_patient_token_is_rejected():
    """TOKEN_B is present in the FILE but not in row 0. A whole-file search
    would accept this; it is exactly the unsound claim."""
    result = verify_csv_claim(CSV_EXPORT, ",", "row:0", TOKEN_B)
    assert not result.verified
    assert result.reason == "CSV_TOKEN_ABSENT_FROM_CLAIMED_ROW"


def test_csv_swapped_pseudonyms_both_fail():
    """Deliberately swapping two patients' pseudonyms between rows must be
    caught on BOTH rows, not silently accepted because both tokens exist
    somewhere in the document."""
    assert not verify_csv_claim(CSV_EXPORT, ",", "row:0", TOKEN_B).verified
    assert not verify_csv_claim(CSV_EXPORT, ",", "row:1", TOKEN_A).verified


def test_csv_row_index_past_end_of_export_is_rejected():
    result = verify_csv_claim(CSV_EXPORT, ",", "row:99", TOKEN_A)
    assert not result.verified
    assert result.reason == "CSV_ROW_INDEX_PAST_END_OF_EXPORT"


def test_csv_header_row_is_never_a_data_row():
    """row:0 is the first DATA row. A claim must not be satisfiable by the
    header line, which is how an off-by-one would hide itself."""
    export = "uhid,patient_ref\n" f"TKN-1,{TOKEN_A}\n"
    assert verify_csv_claim(export, ",", "row:0", TOKEN_A).verified
    assert not verify_csv_claim(export, ",", "row:1", TOKEN_A).verified


def test_csv_quoted_multiline_cell_does_not_shift_row_indices():
    """A quoted embedded newline makes physical lines and logical rows
    diverge. Splitting on newlines would attribute the token to the wrong
    row; the csv parser must keep them aligned."""
    export = (
        "uhid,notes,patient_ref\n"
        'TKN-1,"line one\nline two",' + TOKEN_A + "\n"
        f"TKN-2,plain,{TOKEN_B}\n"
    )
    assert verify_csv_claim(export, ",", "row:0", TOKEN_A).verified
    assert verify_csv_claim(export, ",", "row:1", TOKEN_B).verified
    assert not verify_csv_claim(export, ",", "row:1", TOKEN_A).verified


def test_tsv_delimiter_is_honoured():
    export = f"uhid\tpatient_ref\nTKN-1\t{TOKEN_A}\n"
    assert verify_csv_claim(export, "\t", "row:0", TOKEN_A).verified
    # Parsed with the wrong delimiter the row still contains the token as a
    # substring of one giant cell, so this would pass for the wrong reason --
    # it is the LOCATOR resolution that must stay correct.
    assert verify_csv_claim(export, "\t", "row:1", TOKEN_A).reason == "CSV_ROW_INDEX_PAST_END_OF_EXPORT"


def test_csv_non_row_locator_is_rejected_rather_than_guessed():
    assert verify_csv_claim(CSV_EXPORT, ",", "/0", TOKEN_A).reason == "CSV_LOCATOR_NOT_A_ROW"
    assert verify_csv_claim(CSV_EXPORT, ",", "row:00", TOKEN_A).reason == "CSV_LOCATOR_NOT_A_ROW"
    assert verify_csv_claim(CSV_EXPORT, ",", "row:x", TOKEN_A).reason == "CSV_LOCATOR_NOT_A_ROW"


# --------------------------------------------------------------------------
# JSON: pointer resolution, nesting, escaping, cross-object claims
# --------------------------------------------------------------------------

JSON_EXPORT = json.dumps({
    "_export_meta": {"system": "eHospital", "source_path": "[REDACTED_PATH]"},
    "visits": [
        {"visit_id": "V1", "patient_ref": TOKEN_A},
        {"visit_id": "V2", "patient_ref": TOKEN_B},
    ],
})


def test_json_claim_verifies_in_its_own_object():
    assert verify_json_claim(JSON_EXPORT, "/visits/0", TOKEN_A).verified
    assert verify_json_claim(JSON_EXPORT, "/visits/1", TOKEN_B).verified


def test_json_cross_object_token_is_rejected():
    result = verify_json_claim(JSON_EXPORT, "/visits/0", TOKEN_B)
    assert not result.verified
    assert result.reason == "JSON_TOKEN_ABSENT_FROM_CLAIMED_NODE"


def test_json_swapped_pseudonyms_both_fail():
    assert not verify_json_claim(JSON_EXPORT, "/visits/0", TOKEN_B).verified
    assert not verify_json_claim(JSON_EXPORT, "/visits/1", TOKEN_A).verified


def test_json_pointer_past_end_of_array_is_rejected():
    result = verify_json_claim(JSON_EXPORT, "/visits/9", TOKEN_A)
    assert not result.verified
    assert result.reason == "JSON_POINTER_DOES_NOT_RESOLVE_IN_EXPORT"


def test_json_unparseable_export_is_rejected_not_assumed_clean():
    result = verify_json_claim("{not json", "/visits/0", TOKEN_A)
    assert not result.verified
    assert result.reason == "JSON_EXPORT_UNPARSEABLE"


def test_json_pointer_escapes_tilde_and_slash():
    assert json_pointer_unescape("~1") == "/"
    assert json_pointer_unescape("~0") == "~"
    # Order matters: "~01" is an escaped "~1", NOT a "/" .
    assert json_pointer_unescape("~01") == "~1"
    document = {"a/b": {"c~d": [{"patient_ref": TOKEN_A}]}}
    found, node = resolve_json_pointer(document, "/a~1b/c~0d/0")
    assert found and node["patient_ref"] == TOKEN_A


def test_json_array_index_must_be_canonical():
    """A non-canonical index must not resolve: "/01" silently addressing
    element 1 would let a malformed locator claim the wrong record."""
    document = {"visits": [{"patient_ref": TOKEN_A}, {"patient_ref": TOKEN_B}]}
    assert resolve_json_pointer(document, "/visits/1")[0]
    assert not resolve_json_pointer(document, "/visits/01")[0]
    assert not resolve_json_pointer(document, "/visits/+1")[0]
    assert not resolve_json_pointer(document, "/visits/ 1")[0]


def test_json_nested_wrapper_object_claim():
    """A wrapper around a single object (not a list) addresses the wrapper key
    itself, and the token must live inside that subtree."""
    export = json.dumps({"meta": {"x": 1}, "patient": {"patient_ref": TOKEN_A}})
    assert verify_json_claim(export, "/patient", TOKEN_A).verified
    assert not verify_json_claim(export, "/meta", TOKEN_A).verified


def test_json_token_in_sibling_metadata_does_not_verify_a_record():
    """The token appears in the document, but in the wrapper metadata rather
    than in the claimed record. Whole-file scope would accept it."""
    export = json.dumps({
        "_export_meta": {"note": TOKEN_A},
        "visits": [{"visit_id": "V1"}],
    })
    result = verify_json_claim(export, "/visits/0", TOKEN_A)
    assert not result.verified
    assert result.reason == "JSON_TOKEN_ABSENT_FROM_CLAIMED_NODE"


def test_json_keys_are_not_searched_only_values():
    """A pseudonym is a value. A key that happens to equal it is a structural
    coincidence, not a patient claim."""
    export = json.dumps({"visits": [{TOKEN_A: "something"}]})
    assert not verify_json_claim(export, "/visits/0", TOKEN_A).verified


def test_json_suppressed_entry_shifts_indices_so_out_locator_is_what_matters():
    """Input /visits/2 survives as output /visits/1 after one entry is
    suppressed. Verification addresses the OUTPUT locator; using the input
    locator would check a different patient's record."""
    export = json.dumps({"visits": [{"patient_ref": TOKEN_A}, {"patient_ref": TOKEN_B}]})
    # The record originally at input index 2 is exported at output index 1.
    assert verify_json_claim(export, "/visits/1", TOKEN_B).verified
    assert not verify_json_claim(export, "/visits/2", TOKEN_B).verified


# --------------------------------------------------------------------------
# Document-level claims
# --------------------------------------------------------------------------

def test_document_claim_is_whole_document_scope_by_definition():
    text = f"Patient: {TOKEN_A}\nDISCHARGE SUMMARY\n"
    assert verify_document_claim(text, TOKEN_A).verified
    assert not verify_document_claim(text, TOKEN_B).verified


def test_document_claim_rejection_has_a_reason():
    assert verify_document_claim("no token here", TOKEN_A).reason == "DOC_TOKEN_ABSENT_FROM_EXPORT"


def test_doc_locator_resolves_to_the_whole_json_document():
    export = json.dumps({"resourceType": "Bundle", "entry": [{"patient_ref": TOKEN_A}]})
    found, node = resolve_json_pointer(json.loads(export), "doc")
    assert found and node == json.loads(export)


# --------------------------------------------------------------------------
# Duplicate locators
# --------------------------------------------------------------------------

def test_duplicate_output_locators_are_detected():
    claims = [(1, "row:0"), (1, "row:1"), (1, "row:0"), (2, "row:0")]
    assert find_duplicate_locators(claims) == {(1, "row:0")}


def test_no_duplicates_when_every_claim_is_distinct():
    claims = [(1, "row:0"), (1, "row:1"), (2, "row:0"), (2, "/visits/0")]
    assert find_duplicate_locators(claims) == set()


@pytest.mark.parametrize("locator", ["", "doc"])
def test_empty_and_doc_pointers_both_address_the_root(locator):
    document = {"patient_ref": TOKEN_A}
    found, node = resolve_json_pointer(document, locator)
    assert found and node == document


# --------------------------------------------------------------------------
# End-to-end through the organizer adapter's own claim builder
# --------------------------------------------------------------------------

def test_build_records_jsonl_rejects_a_cross_patient_claim(tmp_path):
    """The integration-level proof: feed the adapter a records index whose
    second claim names the FIRST row's pseudonym. The token exists in the
    exported file, so a whole-file search publishes it; record-specific
    verification must downgrade it to null and say why."""
    from nijsetu.organizer import _build_records_jsonl

    san = tmp_path / "sanitized" / "lab"
    san.mkdir(parents=True)
    (san / "results.csv").write_text(CSV_EXPORT, encoding="utf-8")
    path_map = {7: "lab/results.csv"}
    index = [
        {"file_no": 7, "loc": "row:0", "out_loc": "row:0", "patient_token": TOKEN_A},
        {"file_no": 7, "loc": "row:1", "out_loc": "row:1", "patient_token": TOKEN_A},  # wrong patient
    ]
    lines, rejections = _build_records_jsonl(index, path_map, tmp_path / "sanitized")
    assert lines[0]["patient"] == TOKEN_A
    assert lines[1]["patient"] is None, "a cross-patient claim must not be published"
    assert rejections == {"CSV_TOKEN_ABSENT_FROM_CLAIMED_ROW": 1}


def test_build_records_jsonl_rejects_both_sides_of_a_duplicate_locator(tmp_path):
    """Two records claiming one exported location: there is no evidence which
    is right, so neither is published."""
    from nijsetu.organizer import _build_records_jsonl

    san = tmp_path / "sanitized" / "lab"
    san.mkdir(parents=True)
    (san / "results.csv").write_text(CSV_EXPORT, encoding="utf-8")
    index = [
        {"file_no": 7, "loc": "row:0", "out_loc": "row:0", "patient_token": TOKEN_A},
        {"file_no": 7, "loc": "row:5", "out_loc": "row:0", "patient_token": TOKEN_A},
    ]
    lines, rejections = _build_records_jsonl(index, {7: "lab/results.csv"}, tmp_path / "sanitized")
    assert [line["patient"] for line in lines] == [None, None]
    assert rejections == {"DUPLICATE_OUTPUT_LOCATOR": 2}


def test_build_records_jsonl_publishes_out_only_when_it_differs(tmp_path):
    """SPEC.md: `out` is present only when the output locator differs from the
    input locator."""
    from nijsetu.organizer import _build_records_jsonl

    san = tmp_path / "sanitized" / "lab"
    san.mkdir(parents=True)
    (san / "results.csv").write_text(CSV_EXPORT, encoding="utf-8")
    index = [
        {"file_no": 7, "loc": "row:0", "out_loc": "row:0", "patient_token": TOKEN_A},
        {"file_no": 7, "loc": "row:4", "out_loc": "row:1", "patient_token": TOKEN_B},
    ]
    lines, rejections = _build_records_jsonl(index, {7: "lab/results.csv"}, tmp_path / "sanitized")
    assert rejections == {}
    assert "out" not in lines[0]
    assert lines[1]["out"] == "row:1" and lines[1]["loc"] == "row:4"


def test_build_records_jsonl_makes_no_claim_for_a_quarantined_file(tmp_path):
    """A file that did not survive has no exported record to verify against,
    so it contributes no line at all -- never a line with an unverifiable
    claim attached."""
    from nijsetu.organizer import _build_records_jsonl

    (tmp_path / "sanitized").mkdir()
    index = [{"file_no": 9, "loc": "row:0", "out_loc": "row:0", "patient_token": TOKEN_A}]
    lines, rejections = _build_records_jsonl(index, {}, tmp_path / "sanitized")
    assert lines == []
    assert rejections == {}


def test_build_records_jsonl_rejects_when_the_export_is_unreadable(tmp_path):
    """An export we cannot read back is an abstention with a reason, never a
    trusted claim."""
    from nijsetu.organizer import _build_records_jsonl

    san = tmp_path / "sanitized" / "lab"
    san.mkdir(parents=True)
    (san / "results.csv").write_bytes(b"\xff\xfe\x00broken")
    index = [{"file_no": 7, "loc": "row:0", "out_loc": "row:0", "patient_token": TOKEN_A}]
    lines, rejections = _build_records_jsonl(index, {7: "lab/results.csv"}, tmp_path / "sanitized")
    assert lines[0]["patient"] is None
    assert sum(rejections.values()) == 1
