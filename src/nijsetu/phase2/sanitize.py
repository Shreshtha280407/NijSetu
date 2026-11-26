"""Phase 2 sanitize orchestrator: the full
read -> analyze -> plan -> transform -> stage -> validate -> publish/block path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import resource
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..detect import detect_in_text
from ..detect.paths import REDACTED_PATH, looks_like_path, path_carries_pii
from ..ingest import run_ingest
from ..ingest.encoding import detect_and_decode
from ..ingest.fhir_adapter import _extract_resource, parse_fhir_tree
from ..ingest.header_aliases import resolve_header
from ..ingest.json_adapter import JsonDocumentShape, parse_json_document
from ..identity import resolve_fallback_issuer
from ..ingest.registry import discover_files
from ..plan.builder import analyze_ingest_result
from ..schema import Action, Disposition, FieldRole, SourceCoordinate
from . import keys, risk
from .actions import decide_feature_action
from .clinical_invariants import compare_assertions, extract_assertions
from .date_convention import DateConventionResolver
from .fhir_rewrite import sanitize_fhir_document
from .leak_scan import scan_generic_residual_patterns, scan_literals
from .manifest import build_manifest
from .patient_context import PatientContext, build_patient_contexts
from .policy import PolicyConfig
from .spans import apply_span_replacements, resolve_overlaps
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
    # Populated only when run_sanitize(..., organizer_contract=True): one
    # entry per actually-released record/document, carrying exactly what an
    # external records.jsonl-style audit trail needs (file_no + locator +
    # claimed pseudonym-or-None). None for the default internal contract, so
    # every existing caller/test that never reads this field is unaffected.
    records_index: list[dict] | None = None
    # organizer_contract only: file_no -> every PII literal detected in that
    # input file's own content, for the adapter's filename-safety decision.
    file_literals: dict[int, list[str]] | None = None


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


def _json_pointer_escape(token: str) -> str:
    """RFC 6901: `~` becomes `~0` and `/` becomes `~1`, in that order.

    A wrapper key is attacker-influenced input (it comes from the source
    file), so a key containing a slash would otherwise emit a pointer that
    silently addresses a different, deeper location.
    """
    return token.replace("~", "~0").replace("/", "~1")


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


# File-level JSON wrapper metadata (every top-level key besides the
# configured/auto-detected record-array key) has no single owning patient
# -- DATE and COARSE_GEOGRAPHY are the only two roles whose normal handling
# depends on a specific patient's date-shift/geography-suppression decision
# (see patient_context.PatientContext), so this placeholder is passed to
# decide_feature_action only for roles confirmed NOT to read any of its
# fields (PATIENT_NAME/RELATIVE_NAME/PATIENT_IDENTIFIER/PROVIDER_NAME/
# FACILITY_NAME/CONTACT/FINE_ADDRESS) -- DATE/COARSE_GEOGRAPHY are routed
# around decide_feature_action entirely in _sanitize_wrapper_metadata below.
_METADATA_PLACEHOLDER_CTX = PatientContext(
    cluster_id="__file_metadata__", canonical_key="__file_metadata__", patient_token="",
    date_shift_days=0, reference_event_date=None, age_band=None, age_band_reason="NO_REFERENCE_EVENT",
)
_METADATA_FORCE_SUPPRESS_ROLES = (FieldRole.DATE, FieldRole.COARSE_GEOGRAPHY)


def _sanitize_wrapper_metadata(meta: dict, policy: PolicyConfig, release_key: bytes) -> dict:
    """Sanitizes file-level JSON wrapper metadata IN PLACE instead of
    discarding every record in the file merely because the metadata alone
    carries PII. This is exactly the dataset's documented "hidden leakage"
    trap (PII hidden in export metadata, e.g. a `source_path` embedding a
    patient name, per DATASET.md) -- the organizer's spec expects it
    sanitized, not used as grounds to drop a whole file's clinical content
    (SCORING.md's quarantine-handling and report-honesty criteria both
    penalize exactly that over-suppression).

    DATE/COARSE_GEOGRAPHY content is always suppressed outright (no
    per-patient shift/suppress-geography decision exists at file level;
    this is privacy-conservative, never a utility loss -- an export
    timestamp or a bare district in file bookkeeping is not clinical
    data). Every other role reuses the exact same `decide_feature_action`
    policy decision as every other surface in this module."""
    feats = _reparse_json_value_features(meta)
    feats_by_path: dict[str, list] = defaultdict(list)
    for f in feats:
        feats_by_path[f.source.path].append(f)

    def transform_scalar(value: Any, path: str) -> Any:
        # A path-shaped value is decided as a WHOLE, before any span logic.
        # Span surgery on a path leaves residue: `_` is a word character, so
        # detectors see "Anita_Sharma_010565" as one undelimited token and
        # recognise only part of it. 14 of 16 residual-leak quarantines on
        # the practice data were exactly this field. A provenance path holds
        # no clinical information, so replacing it outright costs nothing --
        # and a path whose components are all innocuous is left untouched.
        if isinstance(value, str) and looks_like_path(value) and path_carries_pii(value, detect_in_text):
            return REDACTED_PATH
        leaf_feats = feats_by_path.get(path, [])
        if not leaf_feats:
            return value
        text = "" if value is None else str(value)
        target = f"@metadata.{path}"
        whole = len(leaf_feats) == 1 and leaf_feats[0].char_start == 0 and leaf_feats[0].char_end == len(text)
        if whole and leaf_feats[0].role in _METADATA_FORCE_SUPPRESS_ROLES:
            return None
        if whole and leaf_feats[0].role != FieldRole.OTHER:
            result = decide_feature_action(leaf_feats[0], policy, _METADATA_PLACEHOLDER_CTX, release_key, target)
            return None if result.omit_field else result.replacement_text
        if not isinstance(value, str):
            return None
        resolved = resolve_overlaps(leaf_feats)
        repls: list[tuple[int, int, str]] = []
        for f in resolved:
            if f.role in _METADATA_FORCE_SUPPRESS_ROLES:
                repls.append((f.char_start, f.char_end, ""))
            else:
                result = decide_feature_action(f, policy, _METADATA_PLACEHOLDER_CTX, release_key, target)
                repls.append((f.char_start, f.char_end, result.replacement_text))
        return apply_span_replacements(value, repls)

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return transform_scalar(node, path)

    return walk(meta, "")


def run_sanitize(
    input_path: Path,
    output_dir: Path,
    policy: PolicyConfig,
    release_id: str,
    master_key: bytes,
    config: AppConfig,
    *,
    organizer_contract: bool = False,
) -> SanitizeOutcome:
    """organizer_contract=False (default): identical behavior to every
    existing caller/test -- NijSetu's own internal release contract
    (released/file_N.ext, whole-release leak-scan block) is completely
    unchanged. organizer_contract=True is an additive, opt-in mode used only
    by the Secret Wards organizer adapter (src/nijsetu/organizer.py): a
    single residual leak quarantines just the implicated file instead of
    blocking the entire release (fail-safe at the smallest safe unit, not
    at the whole-run unit), the claimed patient pseudonym is written into
    every released record, and a records_index audit trail is returned for
    records.jsonl. Nothing here weakens the leak scan itself -- it is the
    same scan, re-run after quarantine to independently confirm the smaller
    release is actually clean before anything is published."""
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
    # Every HMAC-derived value this run mints (patient/identifier tokens,
    # FHIR resource UUIDs) is tracked so the independent leak scan can tell
    # a genuine residual digit pattern apart from a pure coincidence inside
    # our own synthetic output (a v4 UUID's 12-hex-digit last group has a
    # real, non-negligible chance of being all decimal digits, which then
    # reads exactly like a bare Aadhaar number to a context-free regex).
    minted_tokens: set[str] = set()

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
    # Every PII literal actually detected inside each input file, bucketed
    # by file_no: (a) scan_literals below checks each staged output file
    # only against its OWN file's literals, so a name shared between a
    # retained doctor in one file and a different patient's redacted name
    # in another file is never a false leak; (b) organizer_contract reuses
    # the same bucketing to decide whether a file's relative path/filename
    # is safe to preserve (a name/MRN detected in the content is exactly as
    # disqualifying when it reappears in the filename, e.g.
    # "Discharge_Summary_Reena_Nishad_JJH-0076928.txt").
    file_literals: dict[int, list[str]] = defaultdict(list)
    for r in records:
        file_no = int(r.record_key.split(":", 1)[0])
        for f in r.features:
            if f.role in PRIVATE_LEAK_ROLES and f.original_text.strip():
                file_literals[file_no].append(f.original_text.strip())

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

    # Day-first/month-first resolution for numeric dates. Done here, once,
    # over the WHOLE corpus and before any consumer (patient age banding,
    # risk, the transformers, the clinical-invariant snapshot) reads a date:
    # a single value like "03/02/2026" is ambiguous in isolation, but the
    # column/file it sits in almost always contains a value that pins the
    # order. Resolving per value instead of per scope is what previously
    # suppressed 31% of all slash/dash dates outright -- destroying
    # length-of-stay intervals and holding otherwise-publishable records.
    # See phase2.date_convention for the evidence cascade.
    date_convention_reasons: Counter[str] = Counter()
    convention_resolver = DateConventionResolver()
    date_features: list[tuple[int, str, object]] = []
    for r in records:
        file_no = int(r.record_key.split(":", 1)[0])
        for f in r.features:
            if f.role != FieldRole.DATE or not f.original_text.strip():
                continue
            field_path = f.source.path or ""
            convention_resolver.observe(file_no, field_path, f.original_text)
            date_features.append((file_no, field_path, f))
    for file_no, field_path, f in date_features:
        decision = convention_resolver.resolve(file_no, field_path)
        f.date_convention = decision.convention
        date_convention_reasons[decision.reason] += 1

    clusters: dict[str, list[str]] = defaultdict(list)
    for r in records:
        clusters[r.cluster_id].append(r.record_key)
    patient_contexts = build_patient_contexts(clusters, record_features, release_key, policy)
    minted_tokens.update(ctx.patient_token for ctx in patient_contexts.values())

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
    # "Widened", not necessarily "removed": the ladder now replaces a
    # patient's finest coarse level with their district rather than dropping
    # all coarse geography, so the trigger is that the value CHANGED.
    geo_suppress_clusters = {
        cid for cid, s in signatures.items()
        if s.geography is not None and widened_signatures[cid].geography != s.geography
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
    # organizer_contract bookkeeping only (both stay empty otherwise): which
    # released record/document belongs to which file (for the per-file
    # leak-quarantine rollback) and the locator/cluster each one maps to
    # (for records.jsonl, resolved to an actual patient_token right before
    # the final report is assembled).
    released_record_keys_by_file: dict[int, list[str]] = defaultdict(list)
    records_index: list[dict] = []

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

                base_header = result.rows[0] if result.rows else header
                out_rows: list[list[str]] = [[*base_header, "patient_ref"] if organizer_contract else base_header]
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
                    convention_resolver.stamp(fr.file_no, staged_feats)
                    original = extract_assertions(record_features.get(rk, []))
                    staged = extract_assertions(staged_feats)
                    problems = compare_assertions(original, staged, row_ctx[doc.record_no].date_shift_days)
                    if problems:
                        hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    ctx = row_ctx[doc.record_no]
                    out_rows.append([*sanitized_row, ctx.patient_token] if organizer_contract else sanitized_row)
                    kept += 1
                    released_clusters.add(_cluster_of(rk))
                    _record_released_features(rk, staged_feats)
                    if organizer_contract:
                        released_record_keys_by_file[fr.file_no].append(rk)
                        # out_loc is the locator in the EXPORTED document, which
                        # differs from `loc` whenever an earlier row was
                        # suppressed. records.jsonl publishes it as SPEC.md's
                        # `out` field, and the pseudonym verifier uses it to
                        # address this record (and only this record) in the
                        # serialized output. out_rows[0] is the header.
                        records_index.append({
                            "file_no": fr.file_no, "loc": f"row:{doc.record_no}",
                            "out_loc": f"row:{len(out_rows) - 2}", "cluster_id": _cluster_of(rk),
                        })
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
                        file_literals[fr.file_no].extend(
                            f.original_text.strip() for f in meta_feats
                            if f.role in PRIVATE_LEAK_ROLES and f.original_text.strip()
                        )
                        sanitized_meta = _sanitize_wrapper_metadata(shape.wrapper_meta, policy, release_key)
                        residual_feats = _reparse_json_value_features(sanitized_meta)
                        if any(f.role in PRIVATE_LEAK_ROLES for f in residual_feats):
                            # Could not be made safe by the generic metadata
                            # sanitizer above -- fail-safe fallback: hold the
                            # whole file rather than publish a residual leak.
                            hold_index.append(_hold_entry(["JSON_WRAPPER_METADATA_UNSAFE"], file_no=fr.file_no, fmt=fr.format))
                            continue
                        shape = replace(shape, wrapper_meta=sanitized_meta)
                        all_actions.append(Action(
                            target=f"file_{fr.file_no}.@metadata", kind="MASK_SPAN",
                            rule_id="JSON_WRAPPER_METADATA_SANITIZED",
                        ))

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
                    convention_resolver.stamp(fr.file_no, staged_feats)
                    original = extract_assertions(record_features.get(rk, []))
                    problems = compare_assertions(original, extract_assertions(staged_feats), ctx.date_shift_days)
                    if problems:
                        hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                        held_record_count += 1
                        continue
                    if organizer_contract and isinstance(result.value, dict):
                        result.value["patient_ref"] = ctx.patient_token
                    out_records.append(result.value)
                    all_actions.extend(result.actions)
                    released_record_count += 1
                    released_clusters.add(_cluster_of(rk))
                    _record_released_features(rk, staged_feats)
                    if organizer_contract:
                        released_record_keys_by_file[fr.file_no].append(rk)
                        out_index = len(out_records) - 1
                        if shape.kind == "WRAPPED":
                            wrapper_pointer = f"/{_json_pointer_escape(shape.wrapper_key)}"
                            loc = f"{wrapper_pointer}/{doc.record_no}" if shape.wrapped_was_list else wrapper_pointer
                            out_loc = f"{wrapper_pointer}/{out_index}" if shape.wrapped_was_list else wrapper_pointer
                        elif source_is_list:
                            loc, out_loc = f"/{doc.record_no}", f"/{out_index}"
                        else:
                            loc = out_loc = "doc"
                        records_index.append({
                            "file_no": fr.file_no, "loc": loc, "out_loc": out_loc,
                            "cluster_id": _cluster_of(rk),
                        })
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
                convention_resolver.stamp(fr.file_no, staged_feats)
                original = extract_assertions(record_features.get(rk, []))
                problems = compare_assertions(original, extract_assertions(staged_feats), ctx.date_shift_days)
                if problems:
                    hold_index.append(_hold_entry(problems, file_no=fr.file_no, record_no=doc.record_no))
                    held_record_count += 1
                    continue
                final_text = f"Patient: {ctx.patient_token}\n{result.text}" if organizer_contract else result.text
                out_path.write_text(final_text, encoding="utf-8")
                all_actions.extend(result.actions)
                released_file_count += 1
                released_record_count += 1
                released_clusters.add(_cluster_of(rk))
                _record_released_features(rk, staged_feats)
                if organizer_contract:
                    released_record_keys_by_file[fr.file_no].append(rk)
                    records_index.append({
                        "file_no": fr.file_no, "loc": "doc", "out_loc": "doc",
                        "cluster_id": _cluster_of(rk),
                    })

            elif fr.format == "fhir":
                raw = raw_bytes_by_path.get(fr.file_path, b"")
                tree = parse_fhir_tree(raw, config.limits)
                minted_tokens.update(keys.resource_uuid(release_key, f"{fr.file_no}:{doc.record_no}") for doc in docs)
                fhir_resources = [doc.fhir_resource for doc in docs if doc.fhir_resource is not None]
                parse_result = _FhirParseResultShim(fhir_resources)
                fhir_doc = sanitize_fhir_document(
                    tree, fr.file_no, parse_result, record_features, records_by_key, patient_contexts, release_key, policy,
                    held_clusters=frozenset(held_clusters_from_risk), organizer_contract=organizer_contract,
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
                bundle_clusters: set[str] = set()
                for resource_no in fhir_doc.released:
                    rk = f"{fr.file_no}:{resource_no}"
                    if rk in records_by_key:
                        released_clusters.add(_cluster_of(rk))
                        bundle_clusters.add(_cluster_of(rk))
                        staged_fields = _extract_resource(fhir_doc.released_resources[resource_no], resource_no, "", None).fields
                        _record_released_features(rk, staged_fields)
                        if organizer_contract:
                            released_record_keys_by_file[fr.file_no].append(rk)
                if organizer_contract:
                    # Whole-Bundle locator (matches the organizer's own "doc"
                    # convention for FHIR): claim a patient only when every
                    # released resource in this document resolves to the
                    # SAME patient cluster -- a Bundle touching more than one
                    # distinct patient must never make a single confident
                    # whole-document claim (see DATASET.md on FHIR locators).
                    bundle_patient_cluster = next(iter(bundle_clusters)) if len(bundle_clusters) == 1 else None
                    records_index.append({
                        "file_no": fr.file_no, "loc": "doc", "out_loc": "doc",
                        "cluster_id": bundle_patient_cluster,
                    })

        for df in discovered:
            if df.rel_path not in pre_hashes:
                continue
            try:
                post_hash = _sha256_file(df.abs_path)
            except OSError:
                raise SanitizeInputError("SOURCE_INPUT_UNREADABLE_DURING_PROCESSING") from None
            if post_hash != pre_hashes[df.rel_path]:
                raise SanitizeInputError("SOURCE_INPUT_CHANGED_DURING_PROCESSING")

        minted_tokens.update(a.replacement_ref for a in all_actions if a.replacement_ref)
        safe_tokens = frozenset(minted_tokens)
        leak_findings = scan_literals(staging.public, file_literals, safe_tokens) + scan_generic_residual_patterns(staging.public, safe_tokens)

        # organizer_contract: fail-safe at the smallest safe unit. A single
        # residual leak anywhere in the release must not discard every other
        # file that *is* clean -- quarantine exactly the implicated file(s)
        # instead, then independently re-run the SAME two scans over the
        # smaller release to confirm it is actually clean before trusting
        # that. A finding that cannot be attributed to one `released/file_N.*`
        # (should not occur at this point in the pipeline, since report.json/
        # manifest.json/hold_index.json are not written yet -- but checked
        # defensively) falls back to the original whole-release block below,
        # never silently ignored.
        if organizer_contract and leak_findings:
            implicated: set[int] = set()
            unattributed: list[str] = []
            for finding in leak_findings:
                m = re.search(r"released/file_(\d+)\.", finding)
                (implicated.add(int(m.group(1))) if m else unattributed.append(finding))
            if not unattributed:
                for file_no in sorted(implicated):
                    removed = list((staging.public / "released").glob(f"file_{file_no}.*"))
                    if not removed:
                        continue
                    for p in removed:
                        p.unlink()
                    hold_index.append(_hold_entry(["RESIDUAL_LEAK_DETECTED"], file_no=file_no, disposition="QUARANTINED"))
                    released_file_count -= 1
                    for rk in released_record_keys_by_file.get(file_no, []):
                        cid = records_by_key[rk].cluster_id
                        released_record_count -= 1
                        held_record_count += 1
                        released_features.pop(rk, None)
                        if rk in released_members.get(cid, []):
                            released_members[cid].remove(rk)
                        if not released_members.get(cid):
                            released_clusters.discard(cid)
                            held_clusters.add(cid)
                    records_index[:] = [r for r in records_index if r["file_no"] != file_no]
                leak_findings = (
                    scan_literals(staging.public, file_literals, safe_tokens)
                    + scan_generic_residual_patterns(staging.public, safe_tokens)
                )

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
                # How many records ingest parsed out of EACH input file,
                # keyed by file_no as a string (JSON object keys are strings).
                # Published so a caller can account for the records inside a
                # file that was later quarantined as a whole -- without it,
                # those records are absent from every total and the record
                # accounting cannot be reconciled. See organizer.build_output's
                # `records_quarantined`.
                "records_per_file": {
                    str(file_no): count
                    for file_no, count in sorted(Counter(
                        doc.file_no for doc in ingest_result.documents
                    ).items())
                },
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
            # Provenance of every day-first/month-first decision, so a reader
            # can see which dates rest on scope evidence and which on the
            # declared fallback (aggregate counts only, never a value).
            "date_conventions": dict(date_convention_reasons),
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

        resolved_records_index = None
        if organizer_contract:
            resolved_records_index = [
                {
                    "file_no": r["file_no"],
                    "loc": r["loc"],
                    "out_loc": r.get("out_loc", r["loc"]),
                    "patient_token": patient_contexts[r["cluster_id"]].patient_token if r["cluster_id"] else None,
                }
                for r in records_index
            ]

        staging.publish()
        return SanitizeOutcome(
            status, None, report, output_dir, records_index=resolved_records_index,
            file_literals=dict(file_literals) if organizer_contract else None,
        )

    except SanitizeInputError as exc:
        return SanitizeOutcome("BLOCKED", exc.reason_code, {"status": "BLOCKED", "reason_code": exc.reason_code}, None)
    except StagingError as exc:
        return SanitizeOutcome("BLOCKED", exc.reason_code, {"status": "BLOCKED", "reason_code": exc.reason_code}, None)
    finally:
        staging.cleanup()
