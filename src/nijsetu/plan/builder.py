"""Orchestrates ingest -> detect -> identity resolution into an analysis plan.

Produces two artifacts, kept strictly separate (see module docstring in
report.py): a restricted PRIVATE analysis file with spans/evidence/cluster
handles, and a SAFE aggregate report with only counts/status/reason
codes/timing. This is an analysis plan, not a sanitized research release --
Phase 2 performs the actual privacy transformation.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..detect import ALL_NAME_ROUTES, detect_in_text
from ..detect.names import strip_relation_prefix
from ..ingest.header_aliases import is_field_label
from ..identity import resolve_identities
from ..ingest import run_ingest
from ..schema import Feature, FieldRole, SourceCoordinate
from ..translit import consonant_skeleton, tokenize_name

_TRUSTED_ALIAS_ROLES = (
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PROVIDER_NAME, FieldRole.FACILITY_NAME,
)

# Only PERSON-name roles contribute to the corpus name list below. A facility
# name must not: "Jeevan Jyoti Charitable Hospital" would make "Jyoti" a
# corpus-known name and the banner on every one of that hospital's documents
# would be claimed as a patient.
_CORPUS_NAME_TOKEN_ROLES = (
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME,
)
# A one- or two-letter fragment is an initial or a particle, not a name worth
# matching on its own across a whole corpus.
_MIN_CORPUS_NAME_TOKEN = 3


@dataclass
class RecordAnalysis:
    record_key: str
    file_path: str
    format: str
    features: list[Feature]
    link_status: str
    cluster_id: str | None
    reason_codes: list[str]
    fhir_references: list[dict] = field(default_factory=list)


@dataclass
class AnalysisPlan:
    private: dict = field(default_factory=dict)
    safe_report: dict = field(default_factory=dict)


def analyze_ingest_result(
    ingest_result, config: AppConfig, enabled_name_routes: frozenset[str] = ALL_NAME_ROUTES,
) -> tuple[list[RecordAnalysis], dict[str, int]]:
    """The shared detect+link core: given an already-ingested IngestResult,
    returns per-record analysis (features, link status/cluster) plus
    truncated-bucket diagnostics. Used by both Phase 1's run_pipeline (below)
    and Phase 2's sanitize pipeline, which additionally needs the full
    IngestResult.documents (fields/txt/fhir) that this function does not
    consume, to rehydrate sanitized output -- so Phase 2 calls run_ingest
    itself and passes the same result in here, instead of re-deriving
    detection/linking logic independently."""
    trusted_aliases: dict[str, FieldRole] = {}
    # The individual TOKENS of every structurally-declared person name in this
    # corpus, used as a corpus-derived gazetteer (see
    # detect.names.detect_free_text_names's CORPUS_NAME_TOKEN routes).
    #
    # The bundled gazetteer is a fixed list and cannot contain every Indian
    # given name -- "Geeta", "Irfan" and "Rukhsana" are all absent from it,
    # and all three leaked out of Hinglish note bodies ("Geeta ka BP 120/78,
    # BP abhi bhi high hai") where no label or cue precedes the name. But a
    # structured column in the SAME corpus already declares those names, so
    # the corpus itself supplies the list the fixed gazetteer lacks. This is
    # the standard bootstrap for a de-identifier: learn the name list from the
    # data's own labelled fields, which is also why it transfers to hidden
    # datasets whose names this repository has never seen.
    #
    # Whole-string aliases (`trusted_aliases`) are STRUCTURED_ANCHOR tier and
    # win every conflict; these per-token hits are deliberately GAZETTEER tier,
    # the weakest, so an explicit provider cue still wins -- otherwise a
    # patient called Ramesh Kumar elsewhere in the corpus would turn every
    # "Dr. Ramesh Srivastava" into a patient name and destroy the attending
    # physician the utility score expects to survive.
    corpus_name_token_set: set[str] = set()
    for doc in ingest_result.documents:
        for f in doc.fields:
            if f.role in _TRUSTED_ALIAS_ROLES and isinstance(f.value, str) and f.value.strip():
                trusted_aliases[f.value.strip()] = f.role
            if f.role in _CORPUS_NAME_TOKEN_ROLES and isinstance(f.value, str):
                for token in tokenize_name(strip_relation_prefix(f.value)):
                    if len(token) >= _MIN_CORPUS_NAME_TOKEN and not is_field_label(token):
                        corpus_name_token_set.add(token.lower())
                        corpus_name_token_set.add(consonant_skeleton(token))
    corpus_name_tokens = frozenset(t for t in corpus_name_token_set if len(t) >= 2)

    record_features: dict[str, list[Feature]] = {}
    record_sources: dict[str, tuple[str, ...]] = {}
    record_meta: dict[str, tuple[str, str]] = {}  # record_key -> (file_path, format)
    record_fhir_refs: dict[str, list[dict]] = {}
    fhir_patient_links: dict[str, str] = {}
    fhir_ambiguous_links: set[str] = set()

    for doc in ingest_result.documents:
        record_key = f"{doc.file_no}:{doc.record_no}"
        feats: list[Feature] = []
        if doc.txt_document is not None:
            base = SourceCoordinate(doc.file_no, doc.file_path, doc.record_no, "body", char_start=0)
            feats.extend(detect_in_text(
                doc.txt_document.text, base, trusted_aliases, corpus_name_tokens,
                enabled_name_routes=enabled_name_routes,
            ))
        else:
            for fld in doc.fields:
                feats.extend(detect_in_text(
                    fld.original_text, fld.source, trusted_aliases, corpus_name_tokens,
                    structured_role=fld.role,
                    pre_issuer=fld.issuer, pre_scheme=fld.scheme, enabled_name_routes=enabled_name_routes,
                ))
        record_features[record_key] = feats
        record_sources[record_key] = (doc.file_path,)
        record_meta[record_key] = (doc.file_path, doc.format)
        if doc.fhir_resource is not None:
            record_fhir_refs[record_key] = [
                {
                    "raw": r.raw, "kind": r.kind, "field_key": r.field_key, "resolved": r.resolved,
                    "target_resource_no": r.target_resource_no,
                }
                for r in doc.fhir_resource.references
            ]
            if doc.fhir_resource.patient_subject_resource_no is not None:
                fhir_patient_links[record_key] = f"{doc.file_no}:{doc.fhir_resource.patient_subject_resource_no}"
            if doc.fhir_resource.patient_subject_ambiguous:
                fhir_ambiguous_links.add(record_key)

    link_decisions, truncated_buckets = resolve_identities(
        record_features, record_sources, config.scoring, config.limits.max_candidate_bucket,
        config.source_system_map, fhir_patient_links, frozenset(fhir_ambiguous_links),
    )

    records: list[RecordAnalysis] = []
    for key, feats in record_features.items():
        file_path, fmt = record_meta[key]
        decision = link_decisions.get(key)
        records.append(RecordAnalysis(
            record_key=key, file_path=file_path, format=fmt, features=feats,
            link_status=decision.status if decision else "SINGLETON",
            cluster_id=decision.cluster_id if decision else None,
            reason_codes=decision.reason_codes if decision else [],
            fhir_references=record_fhir_refs.get(key, []),
        ))

    return records, truncated_buckets


def run_pipeline(input_path: Path, config: AppConfig, enabled_name_routes: frozenset[str] = ALL_NAME_ROUTES) -> AnalysisPlan:
    """enabled_name_routes restricts which free-text name-detection routes
    run (alias/cue/gazetteer) -- used by scripts/eval_gate1.py to run a real
    alias-only-vs-full detector ablation as two independent passes, never by
    the CLI (which always wants every route)."""
    started = time.monotonic()
    ingest_result = run_ingest(input_path, config)
    records, truncated_buckets = analyze_ingest_result(ingest_result, config, enabled_name_routes)
    elapsed = time.monotonic() - started
    private = _build_private_artifact(records, ingest_result, truncated_buckets)
    safe_report = _build_safe_report(records, ingest_result, truncated_buckets, elapsed)
    return AnalysisPlan(private=private, safe_report=safe_report)


def _build_private_artifact(records, ingest_result, truncated_buckets) -> dict:
    return {
        "label": "PRIVATE - internal analysis artifact - do not publish",
        "records": [
            {
                "record_key": r.record_key,
                "file_path": r.file_path,
                "format": r.format,
                "link_status": r.link_status,
                "cluster_id": r.cluster_id,
                "reason_codes": r.reason_codes,
                "fhir_references": r.fhir_references,
                "features": [
                    {
                        "role": f.role.value,
                        "char_start": f.char_start,
                        "char_end": f.char_end,
                        "original_text": f.original_text,
                        "path": f.source.path,
                        "issuer": f.issuer,
                        "scheme": f.scheme,
                        "confidence_tier": f.confidence_tier,
                        "evidence": [
                            {"rule_id": e.rule_id, "tier": e.tier, "detail_code": e.detail_code}
                            for e in f.evidence
                        ],
                    }
                    for f in r.features
                ],
            }
            for r in records
        ],
        "file_records": [
            {
                "file_no": fr.file_no, "file_path": fr.file_path, "format": fr.format,
                "disposition": fr.disposition.value, "record_count": fr.record_count,
                "reason_codes": fr.reason_codes,
            }
            for fr in ingest_result.file_records
        ],
        "held_records": [
            {"file_no": h.file_no, "file_path": h.file_path, "record_no": h.record_no, "reason_codes": h.reason_codes}
            for h in ingest_result.held_records
        ],
        "truncated_buckets": truncated_buckets,
    }


def _build_safe_report(records, ingest_result, truncated_buckets, elapsed: float) -> dict:
    disposition_counts = Counter(fr.disposition.value for fr in ingest_result.file_records)
    format_counts = Counter(fr.format for fr in ingest_result.file_records)
    role_counts = Counter(f.role.value for r in records for f in r.features)
    tier_counts = Counter(f.confidence_tier for r in records for f in r.features)
    link_status_counts = Counter(r.link_status for r in records)
    reason_code_tally = Counter(code for r in records for code in r.reason_codes)

    accepted_clusters = {r.cluster_id for r in records if r.link_status == "ACCEPTED" and r.cluster_id}
    held_count = link_status_counts.get("HELD", 0)
    # file_no only: a raw (even relative) filename must never land in the safe report.
    unknown_count_files = [fr.file_no for fr in ingest_result.file_records if fr.record_count is None]

    rows = sum(1 for r in records if r.format in ("csv", "tsv"))
    resources = sum(1 for r in records if r.format == "fhir")
    all_refs = [ref for r in records for ref in r.fhir_references]
    refs_resolved = sum(1 for ref in all_refs if ref["resolved"])

    status = "CLEAN"
    if held_count > 0 or len(ingest_result.held_records) > 0 \
            or disposition_counts.get("QUARANTINED", 0) or disposition_counts.get("HELD", 0) \
            or disposition_counts.get("PARSE_FAILED", 0) or disposition_counts.get("UNSUPPORTED", 0):
        status = "HELD_PRESENT"

    return {
        "status": status,
        "counts": {
            "files_discovered": len(ingest_result.file_records),
            "files_by_disposition": dict(disposition_counts),
            "files_by_format": dict(format_counts),
            "files_with_unknown_record_count": unknown_count_files,
            "documents_processed": len(records),
            "rows": rows,
            "fhir_resources": resources,
            "held_rows": len(ingest_result.held_records),
            "patients_accepted_clusters": len(accepted_clusters),
            "records_singleton": link_status_counts.get("SINGLETON", 0),
            "records_held": held_count,
        },
        "fhir": {
            "references_total": len(all_refs),
            "references_resolved": refs_resolved,
            "references_unresolved": len(all_refs) - refs_resolved,
        },
        "detection": {
            "features_by_role": dict(role_counts),
            "features_by_confidence_tier": dict(tier_counts),
        },
        "linking": {
            "status_counts": dict(link_status_counts),
            "reason_code_tally": dict(reason_code_tally),
            "truncated_candidate_buckets": len(truncated_buckets),
        },
        "timing": {"wall_seconds": round(elapsed, 4)},
    }
