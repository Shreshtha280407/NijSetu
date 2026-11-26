"""Generic JSON sanitizing rewriter: rehydrates the real parsed object tree
(nested arrays/objects, booleans, nulls, integers, decimal precision all
preserved by construction, since untouched leaves are the original Python
objects) and rewrites only scalar leaves and dict keys flagged by detection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..detect import detect_in_text
from ..detect.encoded import (
    PAYLOAD_CLEAN,
    PAYLOAD_PII_ONLY,
    SUPPRESSED_PAYLOAD_TEXT,
    classify_payload,
    decode_payload,
    field_suggests_encoded_payload,
    looks_like_encoded_run,
    residual_pii_remains,
)
from ..detect.paths import REDACTED_PATH, looks_like_path, path_carries_pii
from ..schema import Action, FieldRole, SourceCoordinate
from .actions import decide_feature_action
from .patient_context import PatientContext
from .policy import PolicyConfig
from .spans import apply_span_replacements, resolve_overlaps


@dataclass
class JsonTransformResult:
    value: Any
    actions: list[Action]
    held: bool = False
    hold_reasons: list[str] | None = None


def _join_path(base: str, seg: str) -> str:
    return f"{base}.{seg}" if base else seg


def transform_json_record(
    obj: Any,
    record_key: str,
    features: list,
    patient_ctx: PatientContext,
    policy: PolicyConfig,
    release_key: bytes,
) -> JsonTransformResult:
    feats_by_path: dict[str, list] = defaultdict(list)
    for f in features:
        feats_by_path[f.source.path].append(f)

    actions: list[Action] = []
    key_counter = [0]
    seen_keys_per_parent: dict[str, set] = defaultdict(set)
    collided = [False]

    def transform_scalar(value: Any, path: str) -> Any:
        # Path-shaped values are decided as a whole, before span logic, and
        # independently of whether detection flagged anything inside them --
        # see detect.paths for why partial redaction of a path always leaves
        # residue. Applied here as well as to file-level wrapper metadata,
        # since a per-record export block can carry its own source path.
        if isinstance(value, str) and looks_like_path(value) and path_carries_pii(value, detect_in_text):
            actions.append(Action(target=f"{record_key}.{path}", kind="MASK_SPAN", rule_id="PATH_VALUE_REDACTED"))
            return REDACTED_PATH
        # Encoded attachments. Base64 is an encoding, not encryption: the
        # organizer's own scorer decodes every long Base64 run before looking
        # for leaks, so an encoded identifier counts exactly like a plaintext
        # one. Handled before span logic because the spans that matter are
        # inside the DECODED text, not the encoded string.
        if isinstance(value, str):
            replacement = _sanitize_encoded_payload(
                value, path, record_key, patient_ctx, policy, release_key, actions,
            )
            if replacement is not None:
                return replacement
        feats = feats_by_path.get(path, [])
        if not feats:
            return value
        text = "" if value is None else str(value)
        target = f"{record_key}.{path}"
        whole = (
            len(feats) == 1 and feats[0].role != FieldRole.OTHER
            and feats[0].char_start == 0 and feats[0].char_end == len(text)
        )
        if whole:
            result = decide_feature_action(feats[0], policy, patient_ctx, release_key, target)
            actions.append(result.action)
            return None if result.omit_field else result.replacement_text
        if not isinstance(value, str):
            actions.append(Action(target=target, kind="SUPPRESS", rule_id="NON_STRING_SCALAR_FLAGGED"))
            return None
        resolved = resolve_overlaps(feats)
        repls: list[tuple[int, int, str]] = []
        for f in resolved:
            result = decide_feature_action(f, policy, patient_ctx, release_key, target)
            repls.append((f.char_start, f.char_end, result.replacement_text))
            actions.append(result.action)
        return apply_span_replacements(value, repls)

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                key_path = _join_path(path, f"<key:{k}>")
                new_key = k
                if feats_by_path.get(key_path):
                    key_counter[0] += 1
                    new_key = f"SANITIZED_KEY_{key_counter[0]}"
                    actions.append(Action(target=f"{record_key}.{key_path}", kind="TOKENIZE", rule_id="JSON_KEY_RENAMED"))
                if new_key in seen_keys_per_parent[path]:
                    collided[0] = True
                seen_keys_per_parent[path].add(new_key)
                out[new_key] = walk(v, _join_path(path, k))
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return transform_scalar(node, path)

    new_obj = walk(obj, "")
    if collided[0]:
        return JsonTransformResult(value=None, actions=[], held=True, hold_reasons=["JSON_KEY_COLLISION_AFTER_SANITIZATION"])
    return JsonTransformResult(value=new_obj, actions=actions)


# Roles that make a decoded payload privacy-sensitive, and roles that make it
# clinically valuable. Identical to the roles used on every other surface --
# a name is a name whether it arrived as prose, a CSV cell or an attachment.
_PAYLOAD_PII_ROLES = frozenset({
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PATIENT_IDENTIFIER,
    FieldRole.CONTACT, FieldRole.FINE_ADDRESS,
})
_PAYLOAD_CLINICAL_ROLES = frozenset({FieldRole.CLINICAL_MEASUREMENT, FieldRole.CLINICAL_HISTORY})


def _sanitize_encoded_payload(
    value: str,
    path: str,
    record_key: str,
    patient_ctx: PatientContext,
    policy: PolicyConfig,
    release_key: bytes,
    actions: list[Action],
) -> str | None:
    """Returns a replacement for an encoded payload, or None to leave it alone.

    None is returned for anything not confidently an encoded text/JSON payload
    (a short run, a minted token, a hex digest, real binary, mojibake), so a
    value we cannot interpret is never mutated into something invalid.

    Three outcomes once decoded, driven by what the payload CONTAINS:
      * no PII                      -> None (untouched);
      * PII and no clinical content -> the whole payload is replaced. Nothing
        of measured value is lost and no residue is possible;
      * PII and clinical content    -> the decoded text is sanitized span-wise
        and re-encoded in the same alphabet/padding, so clinical values
        survive; then the sanitized text is RE-SCANNED, and if anything PII
        survived that redaction the payload is suppressed outright instead of
        publishing a partially-redacted one.
    """
    if not (looks_like_encoded_run(value) or field_suggests_encoded_payload(path)):
        return None
    payload = decode_payload(value)
    if payload is None:
        return None

    target = f"{record_key}.{path}"
    base = SourceCoordinate(file_no=0, file_path="", record_no=-1, path=f"{path}@decoded")
    classification = classify_payload(
        payload.text, detect_in_text, base, _PAYLOAD_PII_ROLES, _PAYLOAD_CLINICAL_ROLES,
    )
    if classification.kind == PAYLOAD_CLEAN:
        return None

    def _suppress(rule: str) -> str:
        actions.append(Action(target=target, kind="MASK_SPAN", rule_id=rule))
        return payload.reencode(SUPPRESSED_PAYLOAD_TEXT)

    if classification.kind == PAYLOAD_PII_ONLY:
        return _suppress("ENCODED_PAYLOAD_SUPPRESSED_PII_ONLY")

    # PII *and* clinical content: redact the identifying spans in place so the
    # clinical text survives byte-for-byte.
    repls: list[tuple[int, int, str]] = []
    for feature in resolve_overlaps(list(classification.pii_spans)):
        result = decide_feature_action(feature, policy, patient_ctx, release_key, target)
        repls.append((feature.char_start, feature.char_end, result.replacement_text))
        actions.append(result.action)
    sanitized = apply_span_replacements(payload.text, repls)
    if residual_pii_remains(sanitized, detect_in_text, base, _PAYLOAD_PII_ROLES):
        return _suppress("ENCODED_PAYLOAD_SUPPRESSED_RESIDUAL_AFTER_REDACTION")
    actions.append(Action(target=target, kind="MASK_SPAN", rule_id="ENCODED_PAYLOAD_SANITIZED_AND_REENCODED"))
    return payload.reencode(sanitized)
