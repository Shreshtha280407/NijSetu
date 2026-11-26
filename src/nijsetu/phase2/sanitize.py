"""Phase 2 sanitize orchestrator: the full
read -> analyze -> plan -> transform -> stage -> validate -> publish/block path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import resource
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..detect import detect_in_text
from ..ingest import run_ingest
from ..ingest.encoding import detect_and_decode
from ..ingest.fhir_adapter import _extract_resource, parse_fhir_tree
from ..ingest.header_aliases import resolve_header
from ..ingest.json_adapter import JsonDocumentShape, parse_json_document
from ..identity import resolve_fallback_issuer
from ..ingest.registry import discover_files
from ..plan.builder import analyze_ingest_result
from ..schema import Disposition, FieldRole, SourceCoordinate
from . import keys, risk
from .clinical_invariants import compare_assertions, extract_assertions
from .fhir_rewrite import sanitize_fhir_document
from .leak_scan import scan_generic_residual_patterns, scan_literals
from .manifest import build_manifest
from .patient_context import build_patient_contexts
from .policy import PolicyConfig
from .staging import Staging, StagingError, cleanup_stale_staging
from .transform_csv import transform_csv
from .transform_json import transform_json_record
from .transform_txt import transform_txt
from .validate import validate_release

PRIVATE_LEAK_ROLES = (FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PATIENT_IDENTIFIER, FieldRole.CONTACT, FieldRole.FINE_ADDRESS)


def _dump_json_preserving_numbers(obj: Any, depth: int = 0) -> str:
    """json.dumps(..., default=str) turns a retained Decimal leaf (parsed
    with parse_float=Decimal specifically to keep exact source precision,
    e.g. "8.200") into a quoted JSON string instead of a number. Walk the
    tree directly so a Decimal is emitted as the raw numeric literal it
    already is, and every other JSON-native type keeps its own encoding."""
    pad, pad_in = "  " * depth, "  " * (depth + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = ",\n".join(f"{pad_in}{json.dumps(str(k))}: {_dump_json_preserving_numbers(v, depth + 1)}" for k, v in obj.items())
        return "{\n" + items + "\n" + pad + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        items = ",\n".join(f"{pad_in}{_dump_json_preserving_numbers(v, depth + 1)}" for v in obj)
        return "[\n" + items + "\n" + pad + "]"
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, (int, float)):
        return json.dumps(obj)
    return json.dumps(str(obj))


class SanitizeInputError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass
class SanitizeOutcome:
    status: str  # RELEASED | RELEASED_WITH_HOLDS | BLOCKED | ERROR
    reason_code: str | None
    report: dict
    output_dir: Path | None


class _FhirParseResultShim:
    """A minimal stand-in for ingest.fhir_adapter.FhirResult: sanitize_fhir_document
    only reads `.resources`, and Phase 1 already parsed these once per
    resource (DocumentUnit.fhir_resource) -- reused here instead of a second
    parse_fhir() call over the same bytes."""

    def __init__(self, resources: list):
        self.resources = resources


def _hold_entry(
    reasons: list[str], file_no: int | None = None, record_no: int | None = None,
    fmt: str | None = None, disposition: str = "HELD",
) -> dict:
    entry: dict = {"disposition": disposition, "reason_codes": reasons}
    if file_no is not None:
        entry["file_no"] = file_no
    if record_no is not None:
        entry["record_no"] = record_no
    if fmt is not None:
        entry["format"] = fmt
    return entry


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _reparse_csv_row_features(header: list[str], row_values: list[str]) -> list:
    feats = []
    for col_idx, value in enumerate(row_values):
        _canonical, role = resolve_header(header[col_idx] if col_idx < len(header) else "")
        base = SourceCoordinate(file_no=0, file_path="", record_no=0, path=header[col_idx] if col_idx < len(header) else f"col{col_idx}")
        feats.extend(detect_in_text(value, base, structured_role=role))
    return feats


def _reparse_json_value_features(value, path: str = "") -> list:
    feats: list = []
    if isinstance(value, dict):
        for k, v in value.items():
            feats.extend(_reparse_json_value_features(v, f"{path}.{k}" if path else k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            feats.extend(_reparse_json_value_features(item, f"{path}[{i}]"))
    else:
        last_segment = path.rsplit(".", 1)[-1].split("[")[0]
        _canonical, role = resolve_header(last_segment)
        text = "" if value is None else str(value)
        base = SourceCoordinate(file_no=0, file_path="", record_no=0, path=path or "$")
        feats.extend(detect_in_text(text, base, structured_role=role))
    return feats


def run_sanitize(
    input_path: Path,
    output_dir: Path,
    policy: PolicyConfig,
    release_id: str,
    master_key: bytes,
    config: AppConfig,
) -> SanitizeOutcome:
    wall_start = time.monotonic()
    ru_start = resource.getrusage(resource.RUSAGE_SELF)
    cpu_start = ru_start.ru_utime + ru_start.ru_stime

    if not input_path.exists():
        return SanitizeOutcome("ERROR", "INPUT_PATH_NOT_FOUND", {}, None)
    if output_dir.exists() and any(output_dir.iterdir()):
        return SanitizeOutcome("ERROR", "OUTPUT_DIR_NOT_EMPTY", {}, None)

    # Staging lives as a sibling of --output (same filesystem, for an atomic
    # rename) -- if --output were nested inside --input (or vice versa),
    # that hidden staging directory would land inside the very input tree
    # discover_files() just scanned, and the published release would end up
    # nested inside the source data it was sanitized from.
    resolved_input = input_path.resolve()
    resolved_output = output_dir.resolve()
    if (
        resolved_output == resolved_input
        or resolved_input in resolved_output.parents
        or resolved_output in resolved_input.parents
    ):
        return SanitizeOutcome("ERROR", "INPUT_OUTPUT_PATH_OVERLAP", {}, None)

    cleanup_stale_staging(output_dir)
    release_key = keys.derive_release_key(master_key, release_id)

    discovered = discover_files(input_path)
    pre_hashes: dict[str, str] = {}
    raw_bytes_by_path: dict[str, bytes] = {}
    for df in discovered:
        try:
            raw = df.abs_path.read_bytes()
        except OSError:
            continue
        raw_bytes_by_path[df.rel_path] = raw
        pre_hashes[df.rel_path] = hashlib.sha256(raw).hexdigest()

    ingest_result = run_ingest(input_path, config)
    records, _truncated_buckets = analyze_ingest_result(ingest_result, config)
    records_by_key = {r.record_key: r for r in records}
    record_features = {r.record_key: r.features for r in records}

    # A hospital-local identifier (UHID, MRN, billing id, ...) carries no
    # Feature.issuer of its own -- only a national scheme (Aadhaar/ABHA) or
    # an explicit FHIR identifier.system does. Identity resolution already
    # resolves the same local scheme to a concrete issuer (facility name /
    # --source-system config / per-file fallback) for CLUSTERING; without
    # this, tokenization below would scope purely by scheme ("uhid"), so
    # two different hospitals' coincidentally-identical local IDs would
    # tokenize to the exact same public token even though they were
    # correctly kept as different patients.
    for r in records:
        fallback_issuer = None
        for f in r.features:
            if f.role == FieldRole.PATIENT_IDENTIFIER and f.issuer is None and f.scheme:
                if fallback_issuer is None:
                    fallback_issuer = resolve_fallback_issuer(r.features, (r.file_path,), config.source_system_map)
                f.issuer = fallback_issuer

    clusters: dict[str, list[str]] = defaultdict(list)
    for r in records:
        clusters[r.cluster_id].append(r.record_key)
    patient_contexts = build_patient_contexts(clusters, record_features, release_key, policy)

    token_registry = keys.TokenRegistry()
    for _cid, ctx in patient_contexts.items():
        token_registry.check("patient", ctx.patient_token, ctx.canonical_key)

    # Risk analysis + the opt-in small-cohort hold ladder run BEFORE any file
    # is transformed, so a held patient's records are never written at all
    # (not written-then-reported-as-held). Off by default in both presets --
    # see policy.hold_distinctive_singletons.
    signatures = risk.build_signatures(clusters, record_features, patient_contexts)
    risk_before = risk.analyze_risk(signatures, policy.k_threshold)
    widened_signatures, held_clusters_from_risk = risk.escalate_for_small_groups(signatures, policy)

    # Apply the ladder's geography decision to the actual transform.
    # risk_after is independently rebuilt from surviving output values.
    geo_suppress_clusters = {
        cid for cid, s in signatures.items()
        if s.geography is not None and widened_signatures[cid].geography is None
    }
    if geo_suppress_clusters:
        patient_contexts = {
            cid: (replace(ctx, suppress_geography=True) if cid in geo_suppress_clusters else ctx)
            for cid, ctx in patient_contexts.items()
        }

    staging = Staging(output_dir)
    all_actions = []
    hold_index: list[dict] = []
    released_file_count = 0
    released_record_count = 0
    held_record_count = 0
    held_clusters: set[str] = set(held_clusters_from_risk)
    # Tracks every cluster with >=1 record that actually made it into a
    # released file -- a patient can be partly held (some records held,
    # others released); `held_clusters` alone cannot tell "fully held" from
    # "partly held" apart, which previously undercounted patients_released
    # and dropped a partly-held patient's still-visible records out of
    # risk_after entirely.
    released_clusters: set[str] = set()
    released_members: dict[str, list[str]] = defaultdict(list)
    released_features: dict[str, list] = {}

    def _record_released_features(record_key: str, features: list) -> None:
        cid = records_by_key[record_key].cluster_id
        released_members[cid].append(record_key)
        released_features[record_key] = features

    for cid in held_clusters_from_risk:
        hold_index.append({"disposition": "HELD", "reason_codes": ["SMALL_COHORT_RESIDUAL_RISK"]})

    docs_by_file: dict[int, list] = defaultdict(list)
    for doc in ingest_result.documents:
        docs_by_file[doc.file_no].append(doc)

    def _cluster_of(record_key: str) -> str:
        return records_by_key[record_key].cluster_id

    try:
        for h in ingest_result.held_records:
            hold_index.append(_hold_entry(h.reason_codes, file_no=h.file_no, record_no=h.record_no))
            held_record_count += 1

        for fr in ingest_result.file_records:
            if fr.disposition != Disposition.PROCESSED:
                hold_index.append(_hold_entry(fr.reason_codes, file_no=fr.file_no, fmt=fr.format, disposition=fr.disposition.value))
                continue

            docs = sorted(docs_by_file.get(fr.file_no, []), key=lambda d: d.record_no)
            # A synthetic, file_no-based name -- never the original relative
            # path/filename, which could itself carry PII (a patient name or
            # identifier used as a filename, an identifying directory name).
            safe_ext = {"csv": "csv", "tsv": "tsv", "json": "json", "txt": "txt", "fhir": "json"}[fr.format]
            out_path = staging.public / "released" / f"file_{fr.file_no}.{safe_ext}"
            out_path.parent.mkdir(parents=True, exist_ok=True)

            if fr.format in ("csv", "tsv"):
                raw = raw_bytes_by_path.get(fr.file_path, b"")
                text, _enc = detect_and_decode(raw) if raw else ("", "utf-8")
                delimiter = "\t" if fr.format == "tsv" else ","
                # A real csv.reader over the whole text (not a physical-line
                # split) so a quoted multi-line cell's embedded newline never
                # desynchronizes a row index from its logical record.
                rows_raw = list(csv.reader(io.StringIO(text), delimiter=delimiter)) if text.strip() else []
                header = rows_raw[0] if rows_raw else []
                row_fields = [doc.fields for doc in docs]
                row_features = {doc.record_no: record_features.get(f"{fr.file_no}:{doc.record_no}", []) for doc in docs}
                row_ctx = {doc.record_no: patient_contexts[_cluster_of(f"{fr.file_no}:{doc.record_no}")] for doc in docs}
                result = transform_csv(header, row_fields, row_features, row_ctx, policy, release_key, delimiter)
                if result.held:
                    hold_index.append(_hold_entry(result.hold_reasons, file_no=fr.file_no, fmt=fr.format))
                    continue

                out_rows: list[list[str]] = [result.rows[0]] if result.rows else [header]
                kept = 0
                for doc in docs:
                    rk = f"{fr.file_no}:{doc.record_no}"
                    row_idx = doc.record_no + 1
                    if row_idx >= len(result.rows):
                        continue
                    if _cluster_of(rk) in held_clusters_from_risk:
                        hold_index.append(_hold_entry(["SMALL_COHORT_RESIDUAL_RISK"], file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    sanitized_row = result.rows[row_idx]
                    staged_feats = _reparse_csv_row_features(header, sanitized_row)
                    original = extract_assertions(record_features.get(rk, []))
                    staged = extract_assertions(staged_feats)
                    problems = compare_assertions(original, staged, row_ctx[doc.record_no].date_shift_days)
                    if problems:
                        hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    out_rows.append(sanitized_row)
                    kept += 1
                    released_clusters.add(_cluster_of(rk))
                    _record_released_features(rk, staged_feats)
                buf = io.StringIO()
                csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerows(out_rows)
                out_path.write_text(buf.getvalue(), encoding="utf-8")
                all_actions.extend(result.actions)
                released_file_count += 1
                released_record_count += kept

            elif fr.format == "json":
                raw = raw_bytes_by_path.get(fr.file_path, b"")
                if raw:
                    shape = parse_json_document(raw, config.limits, config.record_wrapper_key)
                else:
                    shape = JsonDocumentShape(kind="LIST", wrapper_key=None, wrapper_meta={}, wrapped_was_list=False, records=[])
                tree_records = shape.records
                # A top-level JSON object (not already a list, and not a
                # wrapper around one) must stay a single object on output.
                src_text, _src_enc = detect_and_decode(raw) if raw else ("", "utf-8")
                source_is_list = shape.kind == "LIST" or src_text.lstrip().startswith("[")

                if shape.kind == "WRAPPED":
                    # Wrapper metadata (every top-level key besides the record
                    # list) is retained verbatim only after the same free-text
                    # inspection every other surface gets -- being a
                    # configured wrapper key is not, by itself, evidence the
                    # metadata it carries is safe (e.g. a "notes" key holding
                    # an unexpected patient name).
                    meta_feats = _reparse_json_value_features(shape.wrapper_meta)
                    if any(f.role in PRIVATE_LEAK_ROLES for f in meta_feats):
                        hold_index.append(_hold_entry(["JSON_WRAPPER_METADATA_UNSAFE"], file_no=fr.file_no, fmt=fr.format))
                        continue

                out_records = []
                for doc, obj in zip(docs, tree_records):
                    rk = f"{fr.file_no}:{doc.record_no}"
                    if _cluster_of(rk) in held_clusters_from_risk:
                        hold_index.append(_hold_entry(["SMALL_COHORT_RESIDUAL_RISK"], file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    ctx = patient_contexts[_cluster_of(rk)]
                    result = transform_json_record(obj, rk, record_features.get(rk, []), ctx, policy, release_key)
                    if result.held:
                        hold_index.append(_hold_entry(result.hold_reasons, file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    staged_feats = _reparse_json_value_features(result.value)
                    original = extract_assertions(record_features.get(rk, []))
                    problems = compare_assertions(original, extract_assertions(staged_feats), ctx.date_shift_days)
                    if problems:
                        hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    out_records.append(result.value)
                    all_actions.extend(result.actions)
                    released_record_count += 1
                    released_clusters.add(_cluster_of(rk))
                    _record_released_features(rk, staged_feats)
                if shape.kind == "WRAPPED":
                    if shape.wrapped_was_list:
                        inner: Any = out_records
                    else:
                        inner = out_records[0] if out_records else None
                    output_obj = {**shape.wrapper_meta, shape.wrapper_key: inner}
                elif source_is_list or len(out_records) > 1:
                    output_obj = out_records
                else:
                    output_obj = out_records[0] if out_records else None
                out_path.write_text(_dump_json_preserving_numbers(output_obj), encoding="utf-8")
                released_file_count += 1

            elif fr.format == "txt":
                doc = docs[0] if docs else None
                if doc is None or doc.txt_document is None:
                    out_path.write_text("", encoding="utf-8")
                    released_file_count += 1
                    continue
                rk = f"{fr.file_no}:{doc.record_no}"
                if _cluster_of(rk) in held_clusters_from_risk:
                    hold_index.append(_hold_entry(["SMALL_COHORT_RESIDUAL_RISK"], file_no=fr.file_no, record_no=doc.record_no))
                    held_record_count += 1
                    continue
                ctx = patient_contexts[_cluster_of(rk)]
                result = transform_txt(doc.txt_document.text, record_features.get(rk, []), ctx, policy, release_key, rk)
                staged_feats = detect_in_text(result.text, SourceCoordinate(0, "", 0, "body"))
                original = extract_assertions(record_features.get(rk, []))
                problems = compare_assertions(original, extract_assertions(staged_feats), ctx.date_shift_days)
                if problems:
                    hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                    held_record_count += 1
                    continue
                out_path.write_text(result.text, encoding="utf-8")
                all_actions.extend(result.actions)
                released_file_count += 1
                released_record_count += 1
                released_clusters.add(_cluster_of(rk))
                _record_released_features(rk, staged_feats)

            elif fr.format == "fhir":
                raw = raw_bytes_by_path.get(fr.file_path, b"")
                tree = parse_fhir_tree(raw, config.limits)
                fhir_resources = [doc.fhir_resource for doc in docs if doc.fhir_resource is not None]
                parse_result = _FhirParseResultShim(fhir_resources)
                fhir_doc = sanitize_fhir_document(
                    tree, fr.file_no, parse_result, record_features, records_by_key, patient_contexts, release_key, policy,
                    held_clusters=frozenset(held_clusters_from_risk),
                )
                all_actions.extend(fhir_doc.actions)
                for resource_no, reasons in fhir_doc.held.items():
                    hold_index.append(_hold_entry(reasons, file_no=fr.file_no, record_no=resource_no))
                    held_record_count += 1
                if fhir_doc.tree is None:
                    hold_index.append(_hold_entry(["FHIR_NO_SUPPORTED_RESOURCES_SURVIVED"], file_no=fr.file_no, fmt=fr.format))
                    continue
                out_path.write_text(_dump_json_preserving_numbers(fhir_doc.tree), encoding="utf-8")
                released_file_count += 1
                released_record_count += len(fhir_doc.tree.get("entry", [])) if fhir_doc.tree.get("resourceType") == "Bundle" else 1
                for resource_no in fhir_doc.released:
                    rk = f"{fr.file_no}:{resource_no}"
                    if rk in records_by_key:
                        released_clusters.add(_cluster_of(rk))
                        staged_fields = _extract_resource(fhir_doc.released_resources[resource_no], resource_no, "", None).fields
                        _record_released_features(rk, staged_fields)

        for df in discovered:
            if df.rel_path not in pre_hashes:
                continue
            try:
                post_hash = _sha256_file(df.abs_path)
            except OSError:
                raise SanitizeInputError("SOURCE_INPUT_UNREADABLE_DURING_PROCESSING") from None
            if post_hash != pre_hashes[df.rel_path]:
                raise SanitizeInputError("SOURCE_INPUT_CHANGED_DURING_PROCESSING")

        literals = []
        for r in records:
            for f in r.features:
                if f.role in PRIVATE_LEAK_ROLES and f.original_text.strip():
                    literals.append(f.original_text.strip())
        leak_findings = scan_literals(staging.public, literals) + scan_generic_residual_patterns(staging.public)

        for entry in hold_index:
            if entry.get("record_no") is not None and entry.get("file_no") is not None:
                rk = f"{entry['file_no']}:{entry['record_no']}"
                if rk in records_by_key:
                    held_clusters.add(records_by_key[rk].cluster_id)

        # Recalculated after every hold (ingest-time, clinical-invariant,
        # small-cohort) and the FHIR dependency-closure pass above: a
        # patient with >=1 released record still carries real residual
        # disclosure risk through that record, so they stay IN risk_after --
        # only a patient with ZERO released records (every one of their
        # records held) drops out entirely.
        surviving_signatures = risk.build_released_signatures(released_members, released_features, patient_contexts)
        risk_after = risk.analyze_risk(surviving_signatures, policy.k_threshold)

        action_kind_counts = Counter(a.kind for a in all_actions)
        action_rule_counts = Counter(a.rule_id for a in all_actions)

        elapsed_wall = time.monotonic() - wall_start
        ru_end = resource.getrusage(resource.RUSAGE_SELF)
        cpu_end = ru_end.ru_utime + ru_end.ru_stime

        report = {
            "release_id": release_id,
            "policy": {"name": policy.name, "version": policy.version},
            "counts": {
                "input_files_discovered": len(discovered),
                "files_processed": released_file_count,
                "files_held_or_unsupported": sum(1 for h in hold_index if "file_no" in h and "record_no" not in h),
                "records_released": released_record_count,
                "records_held": held_record_count,
                "patients_total": len(patient_contexts),
                # patients_held: NO record of theirs was released (fully
                # held). patients_released: at least one record was. A
                # partly-held patient (some records held, some released) is
                # counted in patients_released and separately surfaced in
                # patients_partially_held -- never dropped from both.
                "patients_held": len(patient_contexts) - len(released_clusters),
                "patients_released": len(released_clusters),
                "patients_partially_held": len(held_clusters & released_clusters),
            },
            "actions": {"by_kind": dict(action_kind_counts), "by_rule": dict(action_rule_counts)},
            "risk_before": {k: v for k, v in risk_before.__dict__.items()},
            "risk_after": {k: v for k, v in risk_after.__dict__.items()},
            "leak_scan": {"findings": leak_findings, "clean": not leak_findings},
            "token_collisions": token_registry.collisions,
            "hold_summary": dict(Counter(code for h in hold_index for code in h.get("reason_codes", []))),
            "timing": {
                "wall_seconds": round(elapsed_wall, 4),
                "cpu_seconds": round(cpu_end - cpu_start, 4),
                "peak_rss_kb": ru_end.ru_maxrss,
            },
        }

        blocked_reasons = []
        if leak_findings:
            blocked_reasons.append("RESIDUAL_LEAK_DETECTED")
        if token_registry.collisions:
            blocked_reasons.append("TOKEN_COLLISION_DETECTED")
        if released_record_count == 0:
            blocked_reasons.append("NO_RECORDS_SURVIVED_FOR_RELEASE")

        if blocked_reasons:
            report["status"] = "BLOCKED"
            report["reason_codes"] = blocked_reasons
            return SanitizeOutcome("BLOCKED", blocked_reasons[0], report, None)

        # The public status must be finalized and embedded BEFORE report.json
        # is serialized and hashed into the manifest -- otherwise the
        # published report.json never carries a status at all (it was
        # computed too late to be serialized), even though the CLI/manifest
        # may report RELEASED. No count below this point changes after here.
        status = "RELEASED_WITH_HOLDS" if hold_index else "RELEASED"
        report["status"] = status

        (staging.public / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (staging.public / "hold_index.json").write_text(json.dumps(hold_index, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest = build_manifest(staging.public, release_id, policy.name, policy.version)
        (staging.public / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        # Independent final gate: validate the actual staged graph (not the
        # transform's own bookkeeping) the same way a published release would
        # be checked afterward -- a dangling FHIR reference or any other
        # structural problem blocks publication instead of surviving it. This
        # inspects the exact artifact set about to be promoted; nothing here
        # (nor above) mutates any already-checked file afterward.
        staged_check = validate_release(staging.public)
        if not staged_check.passed:
            failing = [c["name"] for c in staged_check.checks if not c["passed"]]
            report["status"] = "BLOCKED"
            report["reason_codes"] = ["STAGED_VALIDATION_FAILED"]
            report["staged_validation_failures"] = failing
            return SanitizeOutcome("BLOCKED", "STAGED_VALIDATION_FAILED", report, None)

        staging.publish()
        return SanitizeOutcome(status, None, report, output_dir)

    except SanitizeInputError as exc:
        return SanitizeOutcome("BLOCKED", exc.reason_code, {"status": "BLOCKED", "reason_code": exc.reason_code}, None)
    except StagingError as exc:
        return SanitizeOutcome("BLOCKED", exc.reason_code, {"status": "BLOCKED", "reason_code": exc.reason_code}, None)
    finally:
        staging.cleanup()
