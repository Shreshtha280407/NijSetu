"""Plain-text (clinical note) sanitizing rewriter: right-to-left span
replacement on original offsets, deterministic overlap resolution, and
everything not flagged retained verbatim."""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import Action
from .actions import decide_feature_action
from .patient_context import PatientContext
from .policy import PolicyConfig
from .spans import apply_span_replacements, resolve_overlaps


@dataclass
class TxtTransformResult:
    text: str
    actions: list[Action]


def transform_txt(
    text: str, features: list, patient_ctx: PatientContext, policy: PolicyConfig,
    release_key: bytes, target_label: str,
) -> TxtTransformResult:
    resolved = resolve_overlaps(features)
    actions: list[Action] = []
    repls: list[tuple[int, int, str]] = []
    for f in resolved:
        result = decide_feature_action(f, policy, patient_ctx, release_key, target_label)
        repls.append((f.char_start, f.char_end, result.replacement_text))
        actions.append(result.action)
    return TxtTransformResult(text=apply_span_replacements(text, repls), actions=actions)
