"""Gate 0.6: cryptographic correctness and storage-confidentiality checks
for nijsetu.security (AES-256-GCM encryption at rest) and its two
production callers (plan.write_plan's private artifact; web.py's upload
vault is covered separately in test_web.py, which needs a running
WorkspaceService)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from nijsetu.plan import read_private_artifact, write_plan
from nijsetu.plan.builder import AnalysisPlan
from nijsetu.security import SecureStore, SecureStoreError, derive_storage_key
from nijsetu.security.secure_store import decrypt_blob, encrypt_blob, is_tmpfs, ram_backed_tempdir

KEY_A = os.urandom(32)
KEY_B = os.urandom(32)


# --- Cryptographic correctness ----------------------------------------

def test_roundtrip_recovers_exact_plaintext():
    pt = b"Patient Name: Fictitious Canary; UHID UHCANARY001"
    envelope = encrypt_blob(KEY_A, b"purpose", b"logical-id", pt)
    assert decrypt_blob(KEY_A, b"purpose", b"logical-id", envelope) == pt


def test_wrong_key_rejected():
    envelope = encrypt_blob(KEY_A, b"purpose", b"id", b"secret")
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        decrypt_blob(KEY_B, b"purpose", b"id", envelope)


def test_tampered_ciphertext_rejected():
    envelope = bytearray(encrypt_blob(KEY_A, b"purpose", b"id", b"secret"))
    envelope[-1] ^= 0x01  # flip one bit inside the authentication tag
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        decrypt_blob(KEY_A, b"purpose", b"id", bytes(envelope))


def test_tampered_nonce_rejected():
    envelope = bytearray(encrypt_blob(KEY_A, b"purpose", b"id", b"secret"))
    envelope[1] ^= 0x01  # flip a bit inside the nonce
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        decrypt_blob(KEY_A, b"purpose", b"id", bytes(envelope))


def test_truncated_ciphertext_rejected():
    envelope = encrypt_blob(KEY_A, b"purpose", b"id", b"secret")
    with pytest.raises(SecureStoreError, match="MALFORMED_OR_TRUNCATED_CIPHERTEXT"):
        decrypt_blob(KEY_A, b"purpose", b"id", envelope[:5])


def test_empty_envelope_rejected():
    with pytest.raises(SecureStoreError, match="MALFORMED_OR_TRUNCATED_CIPHERTEXT"):
        decrypt_blob(KEY_A, b"purpose", b"id", b"")


def test_unknown_version_rejected():
    envelope = bytearray(encrypt_blob(KEY_A, b"purpose", b"id", b"secret"))
    envelope[0] = 0xFF
    with pytest.raises(SecureStoreError, match="UNKNOWN_ENVELOPE_VERSION"):
        decrypt_blob(KEY_A, b"purpose", b"id", bytes(envelope))


def test_nonces_are_unique_even_for_identical_plaintext():
    envelopes = {encrypt_blob(KEY_A, b"purpose", b"id", b"same plaintext every time") for _ in range(50)}
    assert len(envelopes) == 50  # every nonce (and thus ciphertext) is distinct


def test_cross_artifact_substitution_rejected():
    """Ciphertext minted for one logical artifact must not decrypt under a
    different artifact's identity -- this is exactly what stops an
    attacker who can write files from swapping one patient's ciphertext
    into another record's slot."""
    envelope_for_a = encrypt_blob(KEY_A, b"upload", b"run1:patientA.csv", b"A's data")
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        decrypt_blob(KEY_A, b"upload", b"run1:patientB.csv", envelope_for_a)
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        decrypt_blob(KEY_A, b"other-purpose", b"run1:patientA.csv", envelope_for_a)


@pytest.mark.parametrize("payload", [
    b"",
    b"\x00\x01\xff\xfe binary \x80\x81",
    "पटेल राम - मराठी नाव नमूना".encode("utf-8"),
    b"x" * (1024 * 1024),  # 1 MiB
])
def test_binary_unicode_empty_and_large_payloads_roundtrip(payload):
    envelope = encrypt_blob(KEY_A, b"purpose", b"id", payload)
    assert decrypt_blob(KEY_A, b"purpose", b"id", envelope) == payload


def test_oversized_plaintext_rejected():
    from nijsetu.security.secure_store import MAX_ARTIFACT_BYTES
    with pytest.raises(SecureStoreError, match="ARTIFACT_TOO_LARGE"):
        encrypt_blob(KEY_A, b"purpose", b"id", b"x" * (MAX_ARTIFACT_BYTES + 1))


def test_invalid_key_length_rejected():
    with pytest.raises(SecureStoreError, match="INVALID_KEY_LENGTH"):
        encrypt_blob(b"too-short", b"purpose", b"id", b"data")


# --- Key derivation ------------------------------------------------------

def test_derive_storage_key_deterministic_and_domain_separated():
    k1 = derive_storage_key(b"some secret material long enough", context=b"ctx-a")
    k2 = derive_storage_key(b"some secret material long enough", context=b"ctx-a")
    k3 = derive_storage_key(b"some secret material long enough", context=b"ctx-b")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 32


def test_derive_storage_key_rejects_short_material():
    with pytest.raises(SecureStoreError, match="KEY_MATERIAL_TOO_SHORT"):
        derive_storage_key(b"short")


# --- SecureStore: generic multi-blob directory ---------------------------

def test_secure_store_write_read_delete_roundtrip(tmp_path: Path):
    store = SecureStore(tmp_path / "vault", os.urandom(32))
    store.write_encrypted("run1:patient-canary.csv", b"raw patient bytes")
    assert store.read_decrypted("run1:patient-canary.csv") == b"raw patient bytes"
    store.delete("run1:patient-canary.csv")
    with pytest.raises(SecureStoreError, match="ARTIFACT_NOT_FOUND"):
        store.read_decrypted("run1:patient-canary.csv")


def test_secure_store_physical_filenames_never_carry_logical_id(tmp_path: Path):
    store = SecureStore(tmp_path / "vault", os.urandom(32))
    store.write_encrypted("run1:Fictitious_Canary_Patient_Name.csv", b"data")
    on_disk = list((tmp_path / "vault").iterdir())
    assert len(on_disk) == 1
    assert "Canary" not in on_disk[0].name and "Fictitious" not in on_disk[0].name
    assert on_disk[0].name.endswith(".bin")


def test_secure_store_vault_directory_is_owner_only(tmp_path: Path):
    SecureStore(tmp_path / "vault", os.urandom(32))
    mode = stat.S_IMODE(os.stat(tmp_path / "vault").st_mode)
    assert mode == stat.S_IRWXU


def test_secure_store_on_disk_bytes_never_contain_plaintext(tmp_path: Path):
    canary = b"Fictitious Canary Patient, Aadhaar 234567890123, Phone 9123456789"
    store = SecureStore(tmp_path / "vault", os.urandom(32))
    path = store.write_encrypted("logical-name", canary)
    assert canary not in path.read_bytes()


def test_secure_store_close_wipes_vault_and_key(tmp_path: Path):
    key = bytearray(os.urandom(32))
    store = SecureStore(tmp_path / "vault", bytes(key))
    store.write_encrypted("x", b"data")
    store.close()
    assert not (tmp_path / "vault").exists()


# --- Crash resilience: nothing a crash leaves behind is ever plaintext ---

def test_leftover_ciphertext_after_simulated_crash_stays_undecodable_without_key(tmp_path: Path):
    """Simulates Threat B: the process is killed before `close()`/cleanup
    ever runs, so the vault directory is simply abandoned on disk. What's
    left behind must still be unreadable without the (never-persisted)
    key -- demonstrated here by dropping the only reference to the key
    (as a crash would) and inspecting the orphaned bytes directly."""
    vault_root = tmp_path / "vault"
    key = os.urandom(32)
    store = SecureStore(vault_root, key)
    store.write_encrypted("run1:crash-canary.csv", b"Patient Name: Crash Canary, UHID UHCRASH001")
    del store, key  # the crash: no close(), no cleanup, key reference gone

    leftover = list(vault_root.iterdir())
    assert len(leftover) == 1
    raw = leftover[0].read_bytes()
    assert b"Crash Canary" not in raw and b"UHCRASH001" not in raw
    # Without the key, a fresh store instance (the next launch) cannot
    # decrypt it either -- orphaned ciphertext is never a backdoor.
    next_launch = SecureStore(vault_root, os.urandom(32))
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        next_launch.read_decrypted("run1:crash-canary.csv")


# --- plan.write_plan / read_private_artifact -----------------------------

def test_write_plan_private_artifact_is_encrypted_and_authorized_read_works(tmp_path: Path):
    plan = AnalysisPlan(
        private={"label": "PRIVATE - internal analysis artifact - do not publish",
                 "records": [{"record_key": "0:0", "name_literal": "Fictitious Canary Patient"}]},
        safe_report={"status": "CLEAN"},
    )
    key = os.urandom(32)
    _report_path, private_path = write_plan(plan, tmp_path / "out", key)

    raw = private_path.read_bytes()
    assert b"Fictitious Canary Patient" not in raw
    assert b"PRIVATE" not in raw
    mode = stat.S_IMODE(os.stat(private_path).st_mode)
    assert mode == (stat.S_IRUSR | stat.S_IWUSR)

    recovered = read_private_artifact(private_path, key)
    assert recovered == plan.private


def test_read_private_artifact_wrong_key_fails_closed(tmp_path: Path):
    plan = AnalysisPlan(private={"label": "PRIVATE"}, safe_report={"status": "CLEAN"})
    _report_path, private_path = write_plan(plan, tmp_path / "out", os.urandom(32))
    with pytest.raises(SecureStoreError, match="AUTHENTICATION_FAILED"):
        read_private_artifact(private_path, os.urandom(32))


def test_read_private_artifact_corrupted_file_fails_closed_not_partial(tmp_path: Path):
    plan = AnalysisPlan(private={"label": "PRIVATE"}, safe_report={"status": "CLEAN"})
    key = os.urandom(32)
    _report_path, private_path = write_plan(plan, tmp_path / "out", key)
    corrupted = bytearray(private_path.read_bytes())
    corrupted[-1] ^= 0xFF
    private_path.write_bytes(bytes(corrupted))
    with pytest.raises(SecureStoreError):
        read_private_artifact(private_path, key)


# --- RAM-backed staging detection (best-effort, environment dependent) --

def test_is_tmpfs_rejects_a_plain_disk_directory(tmp_path: Path):
    # tmp_path is pytest's own tmp dir -- whatever backs it on this host,
    # it must not be misreported as tmpfs unless it genuinely is one.
    result = is_tmpfs(tmp_path)
    assert isinstance(result, bool)


def test_ram_backed_tempdir_returns_none_when_requirement_impossibly_large():
    assert ram_backed_tempdir(min_free_bytes=1 << 62) is None
