"""Encryption-at-rest for NijSetu's own raw/private working files -- a
cryptographic domain kept strictly separate from `phase2.keys` (which
derives HMAC pseudonymization tokens for the published release). Nothing
here ever informs a patient token, a date shift, or any released byte; it
only protects bytes NijSetu itself writes to local disk before or during
processing (uploaded raw files, the private analysis artifact).

Threat model (see GATE06_SECURITY_FINAL_REPORT.md for the full writeup):
  - Protects against: a stolen disk snapshot/backup, an abnormally
    terminated process leaving ciphertext behind, a local user who can list
    or read working files but not inspect live process memory, and
    tampering with any ciphertext at rest (authenticated encryption fails
    closed).
  - Does NOT protect against: an attacker with root-level control who can
    read the live process's memory while it holds a plaintext buffer or a
    decryption key -- no application-level scheme defeats that, and none of
    the code here claims to.

Algorithm: AES-256-GCM via the `cryptography` package (audited, widely
deployed; no custom cipher/MAC/nonce logic is implemented here). Envelope:

    byte 0       : format version (currently 1)
    bytes 1..13  : 96-bit random nonce (fresh per encryption call, even for
                   identical plaintext -- AES-GCM is catastrophically unsafe
                   under nonce reuse)
    bytes 13..   : AES-GCM ciphertext, with its 16-byte authentication tag
                   appended (the library's own convention)

Associated data (never stored -- both sides recompute it identically from
context they already have) binds each ciphertext to *why* it was written
and *which* logical artifact it is: `purpose` (a short fixed label, e.g.
b"upload" or b"private-analysis") and `logical_id` (the caller's own name
for the artifact, e.g. "<run_id>:<filename>"). Decrypting one artifact's
ciphertext under another artifact's purpose/logical_id fails the GCM tag
check, which is exactly what stops ciphertext minted for one location from
being silently substituted into another.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = 1
NONCE_LEN = 12  # 96 bits, the size AES-GCM is specified and optimized for
TAG_LEN = 16
KEY_LEN = 32  # AES-256
# Defensive ceiling on a single encrypted artifact. Well above anything this
# app ever writes (the dashboard caps a whole dataset at 32 MiB; the private
# analysis artifact is bounded by the same corpus) -- this exists to refuse
# an absurdly large plaintext/ciphertext outright rather than buffer it
# unbounded, not to model a real expected size.
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024

_STORAGE_KEY_LABEL = b"NIJSETU_SECURE_STORE_KDF_V1"


class SecureStoreError(Exception):
    """Reason-code only -- never carries key material, plaintext, or ciphertext."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def derive_storage_key(key_material: bytes, context: bytes = b"") -> bytes:
    """Normalizes arbitrary-length, possibly user-supplied key material (a
    key-file's bytes) into a dedicated 32-byte AES key via one
    domain-separated HMAC-SHA256 call. The label is distinct from every
    label `phase2.keys` uses, so this can never collide with (or be
    mistaken for) a pseudonymization key even if the same underlying secret
    were ever reused -- which callers are told not to do, but a derivation
    step costs nothing and removes the temptation to feed HMAC release-key
    bytes into AES directly."""
    if len(key_material) < 16:
        raise SecureStoreError("KEY_MATERIAL_TOO_SHORT")
    return hmac.new(_STORAGE_KEY_LABEL, context + b"|" + key_material, hashlib.sha256).digest()


def _build_aad(purpose: bytes, logical_id: bytes) -> bytes:
    return (
        bytes([ENVELOPE_VERSION])
        + len(purpose).to_bytes(4, "big") + purpose
        + len(logical_id).to_bytes(4, "big") + logical_id
    )


def encrypt_blob(key: bytes, purpose: bytes, logical_id: bytes, plaintext: bytes) -> bytes:
    """Encrypts `plaintext` into a versioned, self-describing envelope. A
    fresh random nonce is drawn on every call, so encrypting the identical
    plaintext twice (e.g. a re-run) yields different ciphertext -- this is
    deliberate and never a correctness problem, since nothing here is read
    back by byte-equality; see the module docstring on why ciphertext
    nondeterminism is fine even though the published sanitized release
    stays deterministic."""
    if len(key) != KEY_LEN:
        raise SecureStoreError("INVALID_KEY_LENGTH")
    if len(plaintext) > MAX_ARTIFACT_BYTES:
        raise SecureStoreError("ARTIFACT_TOO_LARGE")
    nonce = os.urandom(NONCE_LEN)
    aad = _build_aad(purpose, logical_id)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return bytes([ENVELOPE_VERSION]) + nonce + ciphertext


def decrypt_blob(key: bytes, purpose: bytes, logical_id: bytes, envelope: bytes) -> bytes:
    """Decrypts and authenticates an envelope produced by `encrypt_blob`.
    Fails closed (raises `SecureStoreError`, never returns a partial or
    unauthenticated result) on: an unknown version byte, a
    truncated/malformed envelope, a wrong key, a tampered ciphertext/tag, or
    ciphertext whose purpose/logical_id does not match what the caller
    expects (which is exactly what stops one artifact's ciphertext from
    being substituted into another artifact's slot)."""
    if len(key) != KEY_LEN:
        raise SecureStoreError("INVALID_KEY_LENGTH")
    if len(envelope) > MAX_ARTIFACT_BYTES:
        raise SecureStoreError("ARTIFACT_TOO_LARGE")
    if len(envelope) < 1 + NONCE_LEN + TAG_LEN:
        raise SecureStoreError("MALFORMED_OR_TRUNCATED_CIPHERTEXT")
    version = envelope[0]
    if version != ENVELOPE_VERSION:
        raise SecureStoreError("UNKNOWN_ENVELOPE_VERSION")
    nonce = envelope[1:1 + NONCE_LEN]
    ciphertext = envelope[1 + NONCE_LEN:]
    aad = _build_aad(purpose, logical_id)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        # Never distinguish "wrong key" from "tampered ciphertext" from "AAD
        # mismatch" in the error -- all three are AEAD authentication
        # failures and must fail identically, never falling back to
        # plaintext or a partially-parsed result.
        raise SecureStoreError("AUTHENTICATION_FAILED") from None


class SecureStore:
    """A directory of many independently-encrypted blobs, addressed by a
    caller-chosen `logical_id` that never becomes part of the on-disk
    filename (the physical name is a SHA-256 hash of it) -- so an uploaded
    file's own name, even if it embeds a patient name, never appears in the
    working directory listing. One `SecureStore` holds one in-memory key for
    its own lifetime; the key is never written to disk and is best-effort
    overwritten on `close()` (Python cannot guarantee the underlying memory
    is actually scrubbed -- see the module docstring's threat model)."""

    def __init__(self, root: Path, key: bytes):
        if len(key) != KEY_LEN:
            raise SecureStoreError("INVALID_KEY_LENGTH")
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        self._key = bytearray(key)

    def _physical_path(self, logical_id: str) -> Path:
        if not isinstance(logical_id, str) or not logical_id:
            raise SecureStoreError("INVALID_LOGICAL_ID")
        digest = hashlib.sha256(logical_id.encode("utf-8")).hexdigest()
        path = (self._root / f"{digest}.bin").resolve()
        if path.parent != self._root:
            raise SecureStoreError("INVALID_LOGICAL_ID")  # defensive; sha256 hex cannot traverse
        return path

    def write_encrypted(self, logical_id: str, data: bytes, purpose: bytes = b"blob") -> Path:
        path = self._physical_path(logical_id)
        envelope = encrypt_blob(bytes(self._key), purpose, logical_id.encode("utf-8"), data)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=".tmp-", suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(envelope)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)  # atomic on the same filesystem
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return path

    def read_decrypted(self, logical_id: str, purpose: bytes = b"blob") -> bytes:
        path = self._physical_path(logical_id)
        try:
            envelope = path.read_bytes()
        except FileNotFoundError:
            raise SecureStoreError("ARTIFACT_NOT_FOUND") from None
        return decrypt_blob(bytes(self._key), purpose, logical_id.encode("utf-8"), envelope)

    def delete(self, logical_id: str) -> None:
        self._physical_path(logical_id).unlink(missing_ok=True)

    def close(self) -> None:
        """Best-effort: overwrite the in-memory key bytes and drop every
        ciphertext this store ever wrote. Does not claim the interpreter
        has no other lingering copy of the key (immutable `bytes` objects
        passed in earlier, GC'd buffers, etc.) -- see the module docstring."""
        for i in range(len(self._key)):
            self._key[i] = 0
        shutil.rmtree(self._root, ignore_errors=True)


def is_tmpfs(path: Path) -> bool:
    """True only if `path` resolves onto a filesystem actually mounted as
    `tmpfs` (RAM-backed, never captured by a disk snapshot or backup of
    persistent storage) -- checked against the live mount table rather than
    assumed, because neither `/tmp` nor `/dev/shm` is guaranteed to be
    tmpfs on every host."""
    try:
        target = path.resolve()
    except OSError:
        return False
    best_depth = -1
    best_fstype = None
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if len(fields) < 3:
                    continue
                mount_point, fstype = fields[1], fields[2]
                mp = Path(mount_point)
                try:
                    if target != mp and mp not in target.parents:
                        continue
                except (OSError, ValueError):
                    continue
                depth = len(mp.parts)
                if depth > best_depth:
                    best_depth, best_fstype = depth, fstype
    except OSError:
        return False
    return best_fstype == "tmpfs"


def ram_backed_tempdir(min_free_bytes: int) -> Path | None:
    """Returns a directory verified to be tmpfs-backed with at least
    `min_free_bytes` free, or None if no such location is available on this
    host. Tries the platform temp dir first (often tmpfs, but checked, not
    assumed), then `/dev/shm` explicitly. Callers must treat None as "RAM
    staging is not available here" and fail closed rather than silently
    writing plaintext to ordinary persistent storage."""
    for candidate in (Path(tempfile.gettempdir()), Path("/dev/shm")):
        try:
            if not candidate.is_dir() or not is_tmpfs(candidate):
                continue
            usage = shutil.disk_usage(candidate)
            if usage.free >= min_free_bytes:
                return candidate
        except OSError:
            continue
    return None
