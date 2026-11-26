"""Unit tests: release-scoped keyed pseudonymization (phase2.keys)."""

from __future__ import annotations

import re

import pytest

from nijsetu.phase2 import keys


def test_release_key_is_deterministic_for_same_master_and_release_id():
    k1 = keys.derive_release_key(b"a" * 20, "REL1")
    k2 = keys.derive_release_key(b"a" * 20, "REL1")
    assert k1 == k2


def test_release_key_rotates_with_release_id():
    k1 = keys.derive_release_key(b"a" * 20, "REL1")
    k2 = keys.derive_release_key(b"a" * 20, "REL2")
    assert k1 != k2


def test_release_key_rotates_with_master_key():
    k1 = keys.derive_release_key(b"a" * 20, "REL1")
    k2 = keys.derive_release_key(b"b" * 20, "REL1")
    assert k1 != k2


def test_patient_token_deterministic_and_at_least_128_bits_of_material():
    rk = keys.derive_release_key(b"a" * 20, "REL1")
    t1 = keys.patient_token(rk, "0:0|1:0")
    t2 = keys.patient_token(rk, "0:0|1:0")
    assert t1 == t2
    # base32 alphabet, >=128 bits => at least 26 encoded chars of material
    suffix = t1.split("NS-P-", 1)[1]
    assert re.fullmatch(r"[a-z2-7]+", suffix)
    assert len(suffix) >= 26


def test_identifier_token_differs_by_scheme_and_value():
    rk = keys.derive_release_key(b"a" * 20, "REL1")
    t1 = keys.identifier_token(rk, "uhid", "AR001")
    t2 = keys.identifier_token(rk, "uhid", "AR002")
    t3 = keys.identifier_token(rk, "mrn", "AR001")
    assert len({t1, t2, t3}) == 3


def test_resource_uuid_is_syntactically_valid_uuid_and_deterministic():
    rk = keys.derive_release_key(b"a" * 20, "REL1")
    u1 = keys.resource_uuid(rk, "3:0")
    u2 = keys.resource_uuid(rk, "3:0")
    assert u1 == u2
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", u1)


def test_date_shift_is_deterministic_and_within_configured_range():
    rk = keys.derive_release_key(b"a" * 20, "REL1")
    for key in ("0:0", "1:1", "cluster-x"):
        shift = keys.date_shift_days(rk, key, -30, 30)
        assert -30 <= shift <= 30
        assert keys.date_shift_days(rk, key, -30, 30) == shift


def test_date_shift_rejects_inverted_range():
    rk = keys.derive_release_key(b"a" * 20, "REL1")
    with pytest.raises(ValueError):
        keys.date_shift_days(rk, "x", 10, 5)


def test_load_master_key_rejects_missing_and_short_keys(tmp_path):
    with pytest.raises(keys.KeyError_) as exc:
        keys.load_master_key(tmp_path / "nope.txt")
    assert exc.value.reason_code == "KEY_FILE_NOT_FOUND"

    short = tmp_path / "short.txt"
    short.write_text("tooshort")
    with pytest.raises(keys.KeyError_) as exc:
        keys.load_master_key(short)
    assert exc.value.reason_code == "KEY_MATERIAL_TOO_SHORT"

    ok = tmp_path / "ok.txt"
    ok.write_text("a" * 32)
    assert len(keys.load_master_key(ok)) >= keys.MIN_KEY_BYTES


def test_token_registry_detects_collision_but_not_repeat_of_same_key():
    reg = keys.TokenRegistry()
    reg.check("patient", "TOKEN_A", "source-1")
    reg.check("patient", "TOKEN_A", "source-1")  # same source, same token: fine
    assert not reg.collisions
    reg.check("patient", "TOKEN_A", "source-2")  # different source, same token: collision
    assert reg.collisions == ["patient:TOKEN_A"]
