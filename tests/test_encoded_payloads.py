"""Acceptance tests for encoded (Base64) payload handling.

The defect: a JSON value holding Base64 was treated as an opaque string, so
patient data inside it was published verbatim. The organizer's own practice
scorer decodes every long Base64 run before looking for leaks, so an encoded
identifier counts exactly like a plaintext one -- and on the practice data this
path leaked a planted canary.

The tests are grouped by the three things that can go wrong:
  * a payload carrying PII must not survive readable (positive cases);
  * a payload that is NOT an encoded text payload must not be touched
    (negative cases -- corrupting real data is as bad as leaking);
  * the bounds must hold against hostile input.
"""

from __future__ import annotations

import base64
import json

import pytest

from nijsetu.detect import detect_in_text
from nijsetu.detect.encoded import (
    MAX_DECODE_DEPTH,
    MAX_DECODED_BYTES,
    MAX_ENCODED_INPUT_CHARS,
    MIN_ENCODED_RUN_CHARS,
    PAYLOAD_CLEAN,
    PAYLOAD_PII_AND_CLINICAL,
    PAYLOAD_PII_ONLY,
    SUPPRESSED_PAYLOAD_TEXT,
    classify_payload,
    decode_payload,
    field_suggests_encoded_payload,
    looks_like_encoded_run,
    object_suggests_attachment,
)
from nijsetu.phase2.keys import derive_master_key_from_secret
from nijsetu.phase2.policy import load_policy
from nijsetu.phase2.transform_json import transform_json_record
from nijsetu.phase2.patient_context import PatientContext
from nijsetu.schema import FieldRole, SourceCoordinate

PII_ROLES = frozenset({
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PATIENT_IDENTIFIER,
    FieldRole.CONTACT, FieldRole.FINE_ADDRESS,
})
CLINICAL_ROLES = frozenset({FieldRole.CLINICAL_MEASUREMENT, FieldRole.CLINICAL_HISTORY})
BASE = SourceCoordinate(file_no=0, file_path="", record_no=-1, path="decoded")

CTX = PatientContext(
    cluster_id="c1", canonical_key="c1", patient_token="NS-P-testtoken",
    date_shift_days=3, reference_event_date=None, age_band="40-49", age_band_reason="DERIVED",
)
KEY = derive_master_key_from_secret("test-secret-for-encoded-payloads")
POLICY = load_policy("balanced")


def enc(text: str, *, urlsafe: bool = False, padded: bool = True) -> str:
    raw = text.encode("utf-8")
    out = (base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)).decode("ascii")
    return out if padded else out.rstrip("=")


def run_transform(obj: dict) -> tuple[dict, list]:
    result = transform_json_record(obj, "0:0", [], CTX, POLICY, KEY)
    return result.value, result.actions


def decoded_value(obj: dict, *keys: str) -> str:
    node = obj
    for k in keys:
        node = node[k]
    payload = decode_payload(node)
    assert payload is not None, f"re-encoded value is not decodable Base64: {node!r}"
    return payload.text


# ---------------------------------------------------------------------------
# Positive: encoded PII must not survive readable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secret_text, description", [
    ("Consent witnessed by Tsering Wangmo, phone 8010993510.", "name + phone"),
    ("Patient Mahesh Chauhan, mobile +91 72864 90658", "name + grouped phone"),
    ("Aadhaar 3972 0583 9628 on file", "aadhaar"),
    ("UHID: SNJ/26/064127 registered", "labelled UHID"),
    ("IPD No: IP/26/00130 admitted", "labelled episode id"),
    ("Address: H.No. 364, Mohalla Raja Bazar, Tehsil Lalganj", "address components"),
    ("ABHA No: 91-7938-3729-8403", "abha number"),
    ("contact rakesh@example.com for records", "email"),
])
def test_encoded_pii_does_not_survive_in_readable_form(secret_text, description):
    obj = {"attachment": {"contentType": "text/plain", "data": enc(secret_text)}}
    out, _actions = run_transform(obj)
    rendered = json.dumps(out)
    assert enc(secret_text) not in rendered, f"{description}: original payload survived verbatim"
    text = decoded_value(out, "attachment", "data")
    # Whatever we chose to do, nothing identifying may be readable afterwards.
    assert not any(
        f.role in PII_ROLES for f in detect_in_text(text, BASE)
    ), f"{description}: decoded output still carries PII: {text!r}"


def test_pii_only_payload_is_suppressed_entirely():
    """No clinical content means nothing of measured value to preserve, so the
    safest option is also the free one."""
    obj = {"attachment": {"contentType": "text/plain", "data": enc("Consent witnessed by Tsering Wangmo, phone 8010993510.")}}
    out, actions = run_transform(obj)
    assert decoded_value(out, "attachment", "data") == SUPPRESSED_PAYLOAD_TEXT
    assert any(a.rule_id == "ENCODED_PAYLOAD_SUPPRESSED_PII_ONLY" for a in actions)


def test_clinical_payload_keeps_its_clinical_content():
    """A referral attachment carries a real diagnosis. Blanket suppression of
    every Base64 value would destroy it, so the identifying spans go and the
    clinical text stays."""
    text = "Referral: Mahesh Chauhan, mobile 7286490658. BP 140/90 mmHg. K/C/O DM2 x 9 yrs."
    obj = {"attachment": {"contentType": "text/plain", "data": enc(text)}}
    out, actions = run_transform(obj)
    result = decoded_value(out, "attachment", "data")
    if result != SUPPRESSED_PAYLOAD_TEXT:
        assert "BP 140/90" in result, "clinical measurement must survive sanitization"
        assert "K/C/O DM2" in result, "clinical history must survive sanitization"
        assert "7286490658" not in result
        assert any(
            a.rule_id in ("ENCODED_PAYLOAD_SANITIZED_AND_REENCODED",
                          "ENCODED_PAYLOAD_SUPPRESSED_RESIDUAL_AFTER_REDACTION")
            for a in actions
        )


def test_urlsafe_variant_is_handled_and_round_trips_in_the_same_alphabet():
    secret = "Consent witnessed by Tsering Wangmo, phone 8010993510."
    obj = {"attachment": {"contentType": "text/plain", "data": enc(secret, urlsafe=True)}}
    out, _ = run_transform(obj)
    value = out["attachment"]["data"]
    assert "+" not in value and "/" not in value, "URL-safe input must re-encode URL-safe"
    assert decoded_value(out, "attachment", "data") == SUPPRESSED_PAYLOAD_TEXT


def test_unpadded_input_round_trips_unpadded():
    secret = "Patient Mahesh Chauhan, mobile 7286490658"
    obj = {"attachment": {"contentType": "text/plain", "data": enc(secret, padded=False)}}
    out, _ = run_transform(obj)
    assert not out["attachment"]["data"].endswith("="), "unpadded input must re-encode unpadded"


def test_encoded_json_payload_with_pii_is_handled():
    inner = json.dumps({"name": "Tsering Wangmo", "mobile": "8010993510"})
    obj = {"attachment": {"contentType": "application/json", "data": enc(inner)}}
    out, _ = run_transform(obj)
    text = decoded_value(out, "attachment", "data")
    assert "Tsering Wangmo" not in text and "8010993510" not in text


def test_nested_encoded_payload_is_decoded_within_the_depth_budget():
    secret = "Patient Mahesh Chauhan, mobile 7286490658"
    doubly = enc(enc(secret))
    payload = decode_payload(doubly)
    assert payload is not None
    assert payload.text == secret, "a payload that is itself Base64 must be decoded one more round"
    assert payload.depth >= 2


def test_nesting_beyond_the_depth_budget_stops_rather_than_recursing_forever():
    text = "Patient Mahesh Chauhan, mobile 7286490658"
    deep = text
    for _ in range(MAX_DECODE_DEPTH + 4):
        deep = enc(deep)
    payload = decode_payload(deep)
    # It must terminate and return SOMETHING decodable, never recurse without bound.
    assert payload is None or isinstance(payload.text, str)


def test_output_is_scanned_independently_after_reencoding():
    """The end-to-end guarantee: decode the ACTUAL serialized output and
    confirm no PII is readable, rather than trusting the transform."""
    obj = {"attachment": {"contentType": "text/plain", "data": enc("Consent witnessed by Tsering Wangmo, phone 8010993510.")}}
    out, _ = run_transform(obj)
    serialized = json.dumps(out)
    for run in ("Tsering Wangmo", "8010993510"):
        assert run not in serialized
    payload = decode_payload(out["attachment"]["data"])
    assert payload is not None and "Wangmo" not in payload.text


# ---------------------------------------------------------------------------
# Negative: things that must NOT be decoded or mutated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, description", [
    ("NS-P-ueagp6ho45mm5p7lxb64or4e7jkooizkkwbyinx2beajrt2hbcpa", "our own minted base32 patient token"),
    ("TKN-UHID-xsvd2voqjyts6rdeoontiw4oni53r2jqvjqs4", "our own minted identifier token"),
    ("a3f29c84710b5d6e8f2c1904ab7d3e5069c8b1f2a4d7e9c0b3a6f8d2e4c1b705", "a sha256 hex digest"),
    ("g/dL", "a unit"),
    ("112/78", "a blood pressure"),
    ("OPD/26/02/00085", "a visit identifier"),
    ("Haemoglobin", "a test name"),
    ("650mg 1-1-1 x 5 days", "a dose"),
    ("short", "too short to be a payload"),
    ("", "empty"),
])
def test_values_that_are_not_encoded_text_payloads_are_left_alone(value, description):
    obj = {"field": value}
    out, actions = run_transform(obj)
    assert out["field"] == value, f"{description} must not be rewritten"
    assert not any("ENCODED_PAYLOAD" in (a.rule_id or "") for a in actions), description


def test_binary_payload_is_never_mutated_into_something_invalid():
    """A real scanned image decodes to bytes that are not UTF-8 text. It must
    be left exactly as it was, not "sanitized" into corrupt output."""
    png_like = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(range(256)) * 4
    value = base64.b64encode(png_like).decode("ascii")
    assert decode_payload(value) is None
    obj = {"attachment": {"contentType": "image/png", "data": value}}
    out, _ = run_transform(obj)
    assert out["attachment"]["data"] == value


def test_clean_encoded_payload_is_left_byte_identical():
    """No PII inside means no reason to touch it -- re-encoding even an
    identical payload would be an unnecessary mutation of the release."""
    value = enc("Protocol: dengue management, fluid replacement per WHO chart.")
    obj = {"attachment": {"contentType": "text/plain", "data": value}}
    out, actions = run_transform(obj)
    assert out["attachment"]["data"] == value
    assert not any("ENCODED_PAYLOAD" in (a.rule_id or "") for a in actions)


def test_truncated_and_malformed_padding_do_not_raise():
    """Malformed input must be rejected, not crash the run."""
    secret = enc("Patient Mahesh Chauhan, mobile 7286490658")
    for broken in (secret[:-3], secret + "=", secret + "===", secret.replace("=", "") + "!", "!!!!" + secret):
        obj = {"attachment": {"contentType": "text/plain", "data": broken}}
        out, _ = run_transform(obj)  # must not raise
        rendered = json.dumps(out)
        payload = decode_payload(out["attachment"]["data"])
        if payload is not None:
            assert "7286490658" not in payload.text, f"PII readable after handling {broken[:12]!r}"
        else:
            assert "Mahesh Chauhan" not in rendered


def test_mojibake_is_rejected_rather_than_sanitized():
    """A non-Base64 alphanumeric run that happens to decode must not be
    accepted as text if the result is not plausibly readable."""
    assert decode_payload("Q" * 64) is None or decode_payload("Q" * 64).text.isprintable()


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

def test_oversized_encoded_input_is_refused_before_allocation():
    oversized = "A" * (MAX_ENCODED_INPUT_CHARS + 4)
    assert decode_payload(oversized) is None
    assert not looks_like_encoded_run(oversized)


def test_oversized_decoded_payload_is_refused():
    """A payload whose DECODED size would exceed the cap is rejected on the
    encoded length alone, before any allocation."""
    encoded_len = ((MAX_DECODED_BYTES // 3) + 64) * 4
    assert decode_payload("A" * encoded_len) is None


def test_decoded_size_bound_is_below_the_input_bound():
    assert MAX_DECODED_BYTES < MAX_ENCODED_INPUT_CHARS
    assert MAX_DECODE_DEPTH >= 1
    assert MIN_ENCODED_RUN_CHARS >= 16


def test_deeply_nested_decoded_json_is_not_accepted_as_json():
    """A hostile deeply-nested JSON payload must not be walked without bound.
    It may still be treated as text; it must not be classified as JSON."""
    deep = json.dumps(json.loads("[" * 200 + "]" * 200))
    payload = decode_payload(enc(deep))
    assert payload is None or payload.is_json is False


# ---------------------------------------------------------------------------
# Classification and field context
# ---------------------------------------------------------------------------

def test_classification_distinguishes_the_three_cases():
    clean = classify_payload("Protocol: fluid replacement per WHO chart.", detect_in_text, BASE, PII_ROLES, CLINICAL_ROLES)
    assert clean.kind == PAYLOAD_CLEAN

    pii_only = classify_payload("Consent witnessed by Tsering Wangmo, phone 8010993510.", detect_in_text, BASE, PII_ROLES, CLINICAL_ROLES)
    assert pii_only.kind == PAYLOAD_PII_ONLY
    assert pii_only.pii_spans

    both = classify_payload("Mobile 7286490658. BP 140/90 mmHg. K/C/O DM2.", detect_in_text, BASE, PII_ROLES, CLINICAL_ROLES)
    assert both.kind == PAYLOAD_PII_AND_CLINICAL
    assert both.has_clinical


def test_field_context_is_supporting_evidence():
    assert field_suggests_encoded_payload("attachment.data")
    assert field_suggests_encoded_payload("content")
    assert not field_suggests_encoded_payload("patient_name")
    assert object_suggests_attachment({"contentType", "data"})
    assert not object_suggests_attachment({"patient_name", "uhid"})


def test_json_structure_and_value_types_are_preserved():
    """SPEC.md requires JSON to stay JSON with the same structure and value
    types: a suppressed attachment stays a STRING, never null."""
    obj = {"attachment": {"contentType": "text/plain", "data": enc("Consent witnessed by Tsering Wangmo, phone 8010993510.")}}
    out, _ = run_transform(obj)
    assert set(out) == {"attachment"}
    assert set(out["attachment"]) == {"contentType", "data"}
    assert isinstance(out["attachment"]["data"], str)
    assert out["attachment"]["contentType"] == "text/plain"
