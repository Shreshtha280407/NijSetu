"""Generic JSON sanitizing rewriter: rehydrates the real parsed object tree
(nested arrays/objects, booleans, nulls, integers, decimal precision all
preserved by construction, since untouched leaves are the original Python
objects) and rewrites only scalar leaves and dict keys flagged by detection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..schema import Action, FieldRole
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
