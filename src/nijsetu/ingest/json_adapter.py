"""Generic JSON adapter: depth/size guarded, duplicate-key rejecting, precision preserving."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..config import Limits
from ..errors import LimitExceededError, MalformedInputError
from ..schema import Field, FieldRole, FileRecord, SourceCoordinate, ValueType, Disposition, to_value_type
from .encoding import detect_and_decode
from .header_aliases import resolve_header


@dataclass
class JsonResult:
    file_record: FileRecord
    records: list[list[Field]]


def _scan_depth(text: str, max_depth: int) -> None:
    """Single linear, non-recursive pass that rejects runaway nesting before parsing."""
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > max_depth:
                raise LimitExceededError("JSON_DEPTH_EXCEEDED")
        elif ch in "}]":
            depth = max(0, depth - 1)


def _dup_key_hook(pairs: list[tuple[str, Any]]) -> dict:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise MalformedInputError("DUPLICATE_OBJECT_KEY")
        seen[key] = value
    return seen


def _join_path(base: str, segment: str) -> str:
    return f"{base}.{segment}" if base else segment


def _walk(value: Any, path: str, fields: list[tuple[str, ValueType, Any, str]]) -> None:
    """Iterative walk (explicit stack) collecting (path, type, value, original_text)."""
    stack: list[tuple[Any, str]] = [(value, path)]
    while stack:
        node, node_path = stack.pop()
        if isinstance(node, dict):
            for key in node:
                key_path = _join_path(node_path, f"<key:{key}>")
                fields.append((key_path, ValueType.STRING, key, key))
                stack.append((node[key], _join_path(node_path, key)))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                stack.append((item, f"{node_path}[{i}]"))
        else:
            text = str(node) if node is not None else ""
            fields.append((node_path, to_value_type(node), node, text))


@dataclass
class JsonDocumentShape:
    """Describes the original top-level JSON shape so Phase 2's rewriter can
    reconstruct the same shape on output instead of always flattening to a
    bare array -- in particular a configured `--record-wrapper-key` wrapper
    object (e.g. {"batchLabel": ..., "records": [...]})  must keep its
    wrapper and any other top-level metadata keys, not just the inner
    record list."""

    kind: str  # "LIST" | "OBJECT" | "WRAPPED"
    wrapper_key: str | None
    wrapper_meta: dict[str, Any]  # other top-level keys, original order; {} unless WRAPPED
    wrapped_was_list: bool  # only meaningful when kind == "WRAPPED"
    records: list[Any]


def parse_json_document(
    raw: bytes,
    limits: Limits,
    record_wrapper_key: str | None = None,
) -> JsonDocumentShape:
    """Parses raw JSON bytes into a JsonDocumentShape (lossless: Decimal for
    numbers, dict/list structure intact), without flattening into Fields."""
    if len(raw) > limits.max_json_bytes:
        raise LimitExceededError("JSON_SIZE_EXCEEDED")
    text, _enc = detect_and_decode(raw)
    if text.strip() == "":
        return JsonDocumentShape(kind="LIST", wrapper_key=None, wrapper_meta={}, wrapped_was_list=False, records=[])
    _scan_depth(text, limits.max_json_depth)
    try:
        data = json.loads(text, object_pairs_hook=_dup_key_hook, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise MalformedInputError("JSON_SYNTAX_ERROR", detail=str(exc)) from None

    if record_wrapper_key is not None:
        if not isinstance(data, dict) or record_wrapper_key not in data:
            raise MalformedInputError("RECORD_WRAPPER_NOT_FOUND")
        wrapped = data[record_wrapper_key]
        records = wrapped if isinstance(wrapped, list) else [wrapped]
        wrapper_meta = {k: v for k, v in data.items() if k != record_wrapper_key}
        return JsonDocumentShape("WRAPPED", record_wrapper_key, wrapper_meta, isinstance(wrapped, list), records)
    if isinstance(data, list):
        return JsonDocumentShape("LIST", None, {}, False, data)
    return JsonDocumentShape("OBJECT", None, {}, False, [data])


def parse_json_tree(
    raw: bytes,
    limits: Limits,
    record_wrapper_key: str | None = None,
) -> list[Any]:
    """Parses raw JSON bytes into the list of record-level Python objects
    (lossless: Decimal for numbers, dict/list structure intact), without
    flattening into Fields. Shared by parse_json (Phase 1 detection) and
    Phase 2's rehydrating transform, which needs the real nested structure,
    not a flat field list, to preserve types/precision/shape on output."""
    return parse_json_document(raw, limits, record_wrapper_key).records


def parse_json(
    raw: bytes,
    rel_path: str,
    file_no: int,
    limits: Limits,
    record_wrapper_key: str | None = None,
) -> JsonResult:
    record_objs = parse_json_tree(raw, limits, record_wrapper_key)
    if not record_objs:
        return JsonResult(
            file_record=FileRecord(file_no, rel_path, "json", Disposition.PROCESSED, 0),
            records=[],
        )

    records: list[list[Field]] = []
    for record_no, obj in enumerate(record_objs):
        raw_fields: list[tuple[str, ValueType, Any, str]] = []
        _walk(obj, "", raw_fields)
        fields: list[Field] = []
        for fpath, vtype, fval, ftext in raw_fields:
            last_segment = fpath.rsplit(".", 1)[-1]
            if last_segment.startswith("<key:"):
                # A key is inspected for accidentally-PII-bearing key text
                # (e.g. {"Mohammad Arif": "..."}), never treated as a VALUE
                # of the schema role its own text happens to name -- a
                # legitimate key literally named "patientName"/"uhid" is
                # not itself a patient name/identifier, and must stay OTHER
                # so only free-text detection (gazetteer/cue) can flag it,
                # exactly like any other unexpected key text would be.
                role = FieldRole.OTHER
            else:
                role = resolve_header(last_segment.split("[")[0])[1]
            src = SourceCoordinate(file_no=file_no, file_path=rel_path, record_no=record_no, path=fpath or "$")
            fields.append(Field(source=src, value_type=vtype, value=fval, original_text=ftext, role=role))
        records.append(fields)

    return JsonResult(
        file_record=FileRecord(file_no, rel_path, "json", Disposition.PROCESSED, len(records)),
        records=records,
    )
