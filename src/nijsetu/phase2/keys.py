"""Release-specific keyed pseudonymization: HMAC-SHA256, namespaced tokens.

Reads a local master secret from a file (never printed, never logged), derives
a release-scoped key from it plus the release id, and derives every token
(patient / resource / provider / identifier) and the per-patient date-shift
offset from that release key via separate namespaced HMAC calls. Same input +
key + release id + config => identical tokens; a different release id rotates
every token, since the release key itself changes.

Deliberately deviates from the blueprint's illustrative
`base32(HMAC-SHA256(...))[:16]` (80 bits) -- the task's explicit numeric
requirement is "at least 128 bits of token material", so tokens here encode
the full 32-byte digest (256 bits) in base32, not a 16-character slice.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from pathlib import Path

MIN_KEY_BYTES = 16  # 128 bits


class KeyError_(Exception):
    """Raised for a missing/invalid/too-short master key file. Message is a
    safe reason code only -- never the key material or its path contents."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def load_master_key(key_file: Path) -> bytes:
    if not key_file.is_file():
        raise KeyError_("KEY_FILE_NOT_FOUND")
    try:
        data = key_file.read_bytes()
    except OSError:
        raise KeyError_("KEY_FILE_UNREADABLE") from None
    data = data.strip()
    if len(data) < MIN_KEY_BYTES:
        raise KeyError_("KEY_MATERIAL_TOO_SHORT")
    return data


_ORGANIZER_SECRET_LABEL = b"NIJSETU_ORGANIZER_RAKSHA_SECRET_V1"


def derive_master_key_from_secret(secret: str) -> bytes:
    """Stretches an organizer-supplied `RAKSHA_SECRET` of *any* nonempty
    length into a full 32-byte master key, via one domain-separated HMAC
    call (never the raw secret bytes themselves, and never written back out
    anywhere). This is intentionally a separate code path from
    `load_master_key` (which reads a local key *file* and enforces
    `MIN_KEY_BYTES` on it for the existing internal CLI) -- the organizer's
    secret is an environment value of unknown/variable length (the practice
    value "practice" is 8 bytes), so it is normalized here instead of being
    rejected by the file-oriented length floor. Same secret => same master
    key => same release key => identical tokens/date-shifts; a different
    secret changes every one of them, since HMAC output is uniformly
    sensitive to the key.
    """
    if not secret:
        raise KeyError_("RAKSHA_SECRET_EMPTY")
    return hmac.new(_ORGANIZER_SECRET_LABEL, secret.encode("utf-8"), hashlib.sha256).digest()


def derive_release_key(master_key: bytes, release_id: str) -> bytes:
    if not release_id:
        raise KeyError_("RELEASE_ID_EMPTY")
    return hmac.new(master_key, b"release|" + release_id.encode("utf-8"), hashlib.sha256).digest()


def _scoped_digest(release_key: bytes, namespace: str, value: str) -> bytes:
    """Unambiguous encoding: a length-prefixed namespace so "a|b" in the
    namespace can never collide with a differently-split "a" / "b|..." value."""
    ns = namespace.encode("utf-8")
    val = value.encode("utf-8")
    msg = len(ns).to_bytes(4, "big") + ns + len(val).to_bytes(4, "big") + val
    return hmac.new(release_key, msg, hashlib.sha256).digest()


def _b32(digest: bytes) -> str:
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def patient_token(release_key: bytes, canonical_cluster_key: str) -> str:
    return "NS-P-" + _b32(_scoped_digest(release_key, "patient", canonical_cluster_key))


def resource_token(release_key: bytes, resource_type: str, resource_key: str) -> str:
    prefix = "".join(ch for ch in resource_type if ch.isalnum())[:4].upper() or "RES"
    return f"NS-R-{prefix}-" + _b32(_scoped_digest(release_key, f"resource:{resource_type}", resource_key))


def provider_token(release_key: bytes, provider_key: str) -> str:
    return "NS-D-" + _b32(_scoped_digest(release_key, "provider", provider_key))


def identifier_token(release_key: bytes, scheme: str, value: str, issuer: str | None = None) -> str:
    """A release-specific pseudonym for a hospital-local identifier (UHID,
    MRN, billing/claim/beneficiary id, ...) whose cross-document relationship
    the release still needs to preserve -- never used for a national-scheme
    identifier (Aadhaar/ABHA), which is masked/suppressed outright instead.

    `issuer` folds the resolved hospital/source scope into the HMAC
    namespace (not just the visible TKN-<SCHEME>- prefix, which stays
    scheme-only for readability) -- without it, two different hospitals'
    coincidentally-identical local IDs (e.g. both using "UHID 001") would
    tokenize to the exact same public token even though identity resolution
    correctly kept them as different patients."""
    prefix = "".join(ch for ch in scheme if ch.isalnum())[:6].upper() or "ID"
    namespace = f"identifier:{issuer}:{scheme}" if issuer else f"identifier:{scheme}"
    return f"TKN-{prefix}-" + _b32(_scoped_digest(release_key, namespace, value))


def resource_uuid(release_key: bytes, resource_key: str) -> str:
    """A syntactically valid UUID (urn:uuid: form) for a Bundle entry's
    fullUrl, deterministic under the release key -- version/variant bits are
    forced so the result always parses as a standard UUID, independent of
    the underlying HMAC digest bits."""
    digest = _scoped_digest(release_key, "resource_uuid", resource_key)
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40  # version 4
    raw[8] = (raw[8] & 0x3F) | 0x80  # variant RFC 4122
    return str(uuid.UUID(bytes=bytes(raw)))


def date_shift_days(release_key: bytes, canonical_cluster_key: str, min_days: int, max_days: int) -> int:
    """One deterministic signed offset per confidently-linked patient cluster,
    applied consistently to every supported encounter date for that patient."""
    if max_days < min_days:
        raise ValueError("max_days must be >= min_days")
    span = max_days - min_days + 1
    digest = _scoped_digest(release_key, "dates", canonical_cluster_key)
    val = int.from_bytes(digest[:8], "big") % span
    return min_days + val


class TokenRegistry:
    """Per-run collision check: two distinct source keys must never resolve
    to the same token within one namespace (astronomically unlikely with a
    256-bit digest, but checked explicitly rather than assumed)."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], str] = {}
        self.collisions: list[str] = []

    def check(self, namespace: str, token: str, source_key: str) -> None:
        existing = self._seen.get((namespace, token))
        if existing is not None and existing != source_key:
            self.collisions.append(f"{namespace}:{token}")
        else:
            self._seen[(namespace, token)] = source_key
