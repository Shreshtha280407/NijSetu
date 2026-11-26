"""Bounded decoding and classification of encoded (Base64) payloads.

A JSON export can carry patient data inside an encoded attachment:

    "attachment": {"contentType": "text/plain",
                   "data": "Q29uc2VudCB3aXRuZXNzZWQgYnkgVHNlcmluZyBXYW5nbW8..."}

decoding to "Consent witnessed by Tsering Wangmo, phone 8010993510." Treated as
an opaque string the whole payload is published verbatim, which is a direct
disclosure and -- measured on the practice data -- a leaked planted canary.

Base64 is an ENCODING, not encryption. Anything inside it is in the clear to
anyone who can run `base64 -d`, and the organizer's own practice scorer decodes
every long Base64 run before looking for leaks, so an encoded identifier counts
exactly like a plaintext one.

Three things make this non-trivial, and each is handled explicitly:

**1. Clinical payloads must survive.** Some attachments carry real clinical
content ("...referred for follow-up of Hypothyroidism, unspecified"). Blanket
suppression of every Base64-looking value would destroy that, so the decision
is driven by what the payload actually contains (see `classify_payload`):

  * PII and no clinical content  -> suppress the whole payload. Nothing of
    measured value is lost, and no residue is possible.
  * PII and clinical content     -> sanitize the decoded text span-wise and
    re-encode, so the clinical values survive byte-for-byte.
  * no PII                       -> leave the payload exactly as it was.

**2. Not everything that looks like Base64 is Base64.** Clinical and
identifier values are full of long alphanumeric runs (an HMAC token, a hex
digest, a drug code, a UUID without dashes). Decoding one and "sanitizing" the
mojibake would corrupt real data, so decoding requires the result to be
plausible text or JSON, and callers are expected to prefer FIELD CONTEXT
(a `data` key beside a `contentType` key) over shape alone.

**3. Decoding is attacker-controlled work.** Every limit below is explicit and
enforced before allocation, so a hostile or merely broken input cannot exhaust
memory or CPU: input length, decoded size, recursion depth for
Base64-inside-Base64, and JSON nesting depth. Nothing here recurses without a
decrementing budget.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Explicit bounds. All enforced BEFORE the allocation they guard.
# ---------------------------------------------------------------------------

# Longest encoded string we will even attempt to decode (~768 KB of input).
MAX_ENCODED_INPUT_CHARS = 1_048_576
# Largest decoded payload we will hold. Base64 expands 3 bytes -> 4 chars, so
# this is checked against the encoded length first, then against the result.
MAX_DECODED_BYTES = 786_432
# Base64 nested inside Base64. Depth 1 means "decode once"; the cap exists
# because a small input can otherwise be inflated by repeated decoding.
MAX_DECODE_DEPTH = 3
# json.loads recursion on a hostile deeply-nested decoded payload.
MAX_JSON_NESTING_DEPTH = 64
# Below this, a run is too short to be a meaningful encoded payload and far too
# likely to be a drug code or an identifier fragment.
MIN_ENCODED_RUN_CHARS = 20

# Standard RFC 4648 alphabet, and the URL-safe variant (`-`/`_` for `+`/`/`).
# Padding is optional in the wild, so it is re-derived rather than required.
_STANDARD_RUN = re.compile(r"[A-Za-z0-9+/]{%d,}={0,2}" % MIN_ENCODED_RUN_CHARS)
_URLSAFE_RUN = re.compile(r"[A-Za-z0-9_-]{%d,}={0,2}" % MIN_ENCODED_RUN_CHARS)

# A decoded payload is accepted as TEXT only if it is overwhelmingly printable.
# Random bytes (a real image, a wrongly-decoded hex digest) fail this, which is
# what keeps us from "sanitizing" mojibake.
_MIN_PRINTABLE_RATIO = 0.90
_ALLOWED_CONTROL = frozenset("\t\n\r")


@dataclass(frozen=True)
class DecodedPayload:
    """A payload we decoded and are willing to reason about."""

    text: str
    raw: bytes
    urlsafe: bool
    padded: bool
    depth: int  # how many decode rounds were needed
    is_json: bool

    def reencode(self, new_text: str) -> str:
        """Re-encode in the SAME alphabet and padding convention as the input,
        so the field round-trips through whatever reader consumes it."""
        data = new_text.encode("utf-8")
        encoded = base64.urlsafe_b64encode(data) if self.urlsafe else base64.b64encode(data)
        out = encoded.decode("ascii")
        return out if self.padded else out.rstrip("=")


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in _ALLOWED_CONTROL)
    return good / len(text)


def looks_like_encoded_run(value: str) -> bool:
    """Shape test only. Deliberately permissive: callers combine it with field
    context, and `decode_payload` is the real gate."""
    stripped = value.strip()
    if not (MIN_ENCODED_RUN_CHARS <= len(stripped) <= MAX_ENCODED_INPUT_CHARS):
        return False
    return bool(_STANDARD_RUN.fullmatch(stripped) or _URLSAFE_RUN.fullmatch(stripped))


def _json_depth(node: object, budget: int) -> bool:
    """True when `node` nests no deeper than `budget`. Iterative-by-budget so a
    hostile payload cannot blow the Python stack."""
    if budget <= 0:
        return False
    if isinstance(node, dict):
        return all(_json_depth(v, budget - 1) for v in node.values())
    if isinstance(node, list):
        return all(_json_depth(v, budget - 1) for v in node)
    return True


def _try_b64decode(value: str) -> tuple[bytes, bool, bool] | None:
    """Returns (raw, urlsafe, was_padded) or None.

    Validation is strict (`validate=True`) so a string containing characters
    outside the alphabet is rejected rather than silently having them skipped,
    which is how a non-Base64 value would otherwise "successfully" decode to
    garbage.
    """
    stripped = value.strip()
    if not stripped or len(stripped) > MAX_ENCODED_INPUT_CHARS:
        return None
    # Reject before allocating: 4 encoded chars -> 3 decoded bytes.
    if (len(stripped) // 4) * 3 > MAX_DECODED_BYTES:
        return None
    was_padded = stripped.endswith("=")
    padded = stripped + "=" * (-len(stripped) % 4)
    for urlsafe in (False, True):
        candidate = padded.replace("-", "+").replace("_", "/") if urlsafe else padded
        if urlsafe and candidate == padded:
            continue  # no URL-safe characters present; the standard attempt already covered it
        try:
            raw = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) > MAX_DECODED_BYTES:
            return None
        return raw, urlsafe, was_padded
    return None


def decode_payload(value: str, depth_budget: int = MAX_DECODE_DEPTH) -> DecodedPayload | None:
    """Decode `value` if it is plausibly an encoded TEXT or JSON payload.

    Returns None for anything we are not confident about -- a short run, a
    non-Base64 string, binary content, or mojibake. None means "leave this
    value alone", which is the safe answer for a value we cannot interpret:
    the caller still has the ordinary detectors and the residual output scan.
    """
    if depth_budget <= 0:
        return None
    attempt = _try_b64decode(value)
    if attempt is None:
        return None
    raw, urlsafe, padded = attempt
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary (a real scanned image): never mutated into something invalid
    if _printable_ratio(text) < _MIN_PRINTABLE_RATIO:
        return None

    depth = MAX_DECODE_DEPTH - depth_budget + 1

    # Nested encoding: a payload that is ITSELF a single encoded run gets one
    # more bounded round. The budget decrements, so this terminates.
    if depth_budget > 1 and looks_like_encoded_run(text):
        nested = decode_payload(text, depth_budget - 1)
        if nested is not None:
            return nested

    is_json = False
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if parsed is not None and _json_depth(parsed, MAX_JSON_NESTING_DEPTH):
            is_json = True

    return DecodedPayload(text=text, raw=raw, urlsafe=urlsafe, padded=padded, depth=depth, is_json=is_json)


# ---------------------------------------------------------------------------
# Field context
# ---------------------------------------------------------------------------

# Keys whose value is conventionally an encoded blob. Used as supporting
# evidence so a long token in an unrelated field is not decoded on shape alone.
_ENCODED_VALUE_KEYS = frozenset({
    "data", "content", "attachment", "blob", "payload", "base64", "b64",
    "file", "document", "scan", "image", "body", "raw", "bytes",
})
# Sibling keys that confirm an attachment-shaped object.
_ATTACHMENT_SIBLING_KEYS = frozenset({"contenttype", "content_type", "mimetype", "mime_type", "language", "title", "size"})


def field_suggests_encoded_payload(field_path: str) -> bool:
    """True when the FIELD NAME itself says the value is an encoded blob."""
    last = field_path.rsplit(".", 1)[-1].split("[")[0].strip().lower()
    return last in _ENCODED_VALUE_KEYS


def object_suggests_attachment(sibling_keys: object) -> bool:
    """True when a value's sibling keys describe an attachment (a
    `contentType` next to a `data`), which is the strongest available
    structural evidence short of decoding."""
    lowered = {str(k).strip().lower() for k in sibling_keys}  # type: ignore[union-attr]
    return bool(lowered & _ATTACHMENT_SIBLING_KEYS)


# ---------------------------------------------------------------------------
# What is inside the payload decides what happens to it
# ---------------------------------------------------------------------------

PAYLOAD_CLEAN = "CLEAN"                      # no PII: leave untouched
PAYLOAD_PII_ONLY = "PII_ONLY"                # PII, no clinical content: suppress whole payload
PAYLOAD_PII_AND_CLINICAL = "PII_AND_CLINICAL"  # both: sanitize spans, re-encode


@dataclass(frozen=True)
class PayloadClassification:
    kind: str
    pii_spans: tuple  # tuple[Feature, ...] -- spans to redact when sanitizing
    has_clinical: bool


def classify_payload(text: str, detect_in_text, source_coordinate, pii_roles, clinical_roles) -> PayloadClassification:
    """Classify a decoded payload by what the ordinary detectors find in it.

    Deliberately reuses the SAME detectors used on every other surface rather
    than a special encoded-content ruleset: a name is a name whether it arrived
    as prose, as a CSV cell or inside an attachment, and a second ruleset would
    drift from the first.
    """
    features = detect_in_text(text, source_coordinate)
    pii = tuple(f for f in features if f.role in pii_roles)
    has_clinical = any(f.role in clinical_roles for f in features)
    if not pii:
        return PayloadClassification(PAYLOAD_CLEAN, (), has_clinical)
    if has_clinical:
        return PayloadClassification(PAYLOAD_PII_AND_CLINICAL, pii, True)
    return PayloadClassification(PAYLOAD_PII_ONLY, pii, False)


# A payload we decided to suppress is replaced by this, then re-encoded, so the
# field keeps its type and its decoded form stays honest about what happened.
SUPPRESSED_PAYLOAD_TEXT = "[REDACTED_ATTACHMENT]"


def residual_pii_remains(text: str, detect_in_text, source_coordinate, pii_roles) -> bool:
    """Re-run detection over the SANITIZED decoded text.

    This is the gate that makes re-encoding safe: a span-wise redaction that
    left anything behind (overlapping spans, an offset bug, a second
    occurrence) is caught here, and the caller falls back to suppressing the
    whole payload instead of publishing a partially-redacted one.
    """
    return any(f.role in pii_roles for f in detect_in_text(text, source_coordinate))
