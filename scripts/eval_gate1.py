#!/usr/bin/env python3
"""Measured Gate 1 evidence: entity P/R/F1 (one-to-one, real span coordinates),
a genuine alias-only-vs-full detector ablation (two independent passes, not a
post-hoc filter of one run), pairwise + whole-group link precision/recall,
and candidate recall -- computed against the labelled synthetic fixtures
(gold labels declared here, since this repo authored the fixtures and
therefore knows ground truth for them).

Writes JSON to stdout; also usable as a library by the final report step.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nijsetu.config import AppConfig  # noqa: E402
from nijsetu.detect import detect_in_text  # noqa: E402
from nijsetu.gate.phase1 import DEV_SOURCE_SYSTEM_MAP  # noqa: E402
from nijsetu.identity import build_candidate_features  # noqa: E402
from nijsetu.identity.candidates import generate_candidate_pairs  # noqa: E402
from nijsetu.ingest import run_ingest  # noqa: E402
from nijsetu.plan import run_pipeline  # noqa: E402
from nijsetu.schema import FieldRole, SourceCoordinate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV = REPO_ROOT / "tests" / "fixtures" / "dev"
HELDOUT = REPO_ROOT / "tests" / "fixtures" / "heldout"
EVAL_CONFIG = AppConfig(source_system_map=DEV_SOURCE_SYSTEM_MAP)


@dataclass(frozen=True)
class GoldSpan:
    """One labelled entity. structured fields always start at offset 0
    within their own field value (how detect_in_text spans structured
    roles) -- char_start/char_end are required only for freetext spans,
    independently located in the raw fixture text (see README at the
    bottom of this file), never derived from the detector's own output."""

    file_path: str
    role: str
    text: str
    structure: str  # "structured" / "freetext"
    lang: str
    record_no: int = 0
    char_start: int | None = None
    char_end: int | None = None

    def expected_span(self) -> tuple[int, int]:
        if self.structure == "structured":
            return 0, len(self.text)
        assert self.char_start is not None and self.char_end is not None, \
            f"freetext gold span {self!r} needs explicit char_start/char_end"
        return self.char_start, self.char_end


# -- structured, English -------------------------------------------------------
GOLD_ENTITIES: list[GoldSpan] = [
    GoldSpan("csv_basic/registration.csv", "PATIENT_NAME", "Ramesh Kumar", "structured", "en", record_no=0),
    GoldSpan("csv_basic/registration.csv", "PATIENT_NAME", "Sita Devi", "structured", "en", record_no=1),
    GoldSpan("fhir/patient_encounter.json", "PATIENT_NAME", "Farhan Qureshi", "structured", "en"),
    GoldSpan("arif_corroborated/registration.csv", "PATIENT_NAME", "Mohammad Arif", "structured", "en"),
    GoldSpan("meera_corroborated/registration.csv", "PATIENT_NAME", "Meera Sharma", "structured", "en"),
    # -- free text, English/Hinglish, cue- or gazetteer-supported --
    GoldSpan("arif_corroborated/opd_note.txt", "PATIENT_NAME", "Arif", "freetext", "hinglish", char_start=4, char_end=8),
    GoldSpan("arif_corroborated/discharge.txt", "PATIENT_NAME", "Md. Arif", "freetext", "en", char_start=27, char_end=35),
    GoldSpan("txt_notes/clinical_semantics.txt", "RELATIVE_NAME", "Ramesh", "freetext", "en", char_start=26, char_end=32),
    GoldSpan("txt_notes/clinical_semantics.txt", "PROVIDER_NAME", "Ramesh", "freetext", "en", char_start=103, char_end=109),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_HISTORY", "C/O", "freetext", "en", char_start=46, char_end=49),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT", "Ramipril", "freetext", "en", char_start=143, char_end=151),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT", "5 mg twice", "freetext", "en", char_start=152, char_end=162),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT", "HbA1c 8.2%", "freetext", "en", char_start=190, char_end=200),
    GoldSpan("txt_notes/provider_patient_collision.txt", "PROVIDER_NAME", "Ramesh", "freetext", "en", char_start=16, char_end=22),
    GoldSpan("txt_notes/provider_patient_collision.txt", "PATIENT_NAME", "Ramesh Chandra", "freetext", "en", char_start=32, char_end=46),
    # -- Devanagari: "patientName" (camelCase JSON key) now correctly
    # resolves to a header-aliased PATIENT_NAME field (see
    # header_aliases.normalize_header's camelCase/PascalCase boundary
    # splitting), so the whole field value is one trivial structured span.
    GoldSpan("arif_corroborated/consultation.json", "PATIENT_NAME", "मोहम्मद आरिफ़", "structured", "hi"),
    GoldSpan("meera_corroborated/consultation.json", "PATIENT_NAME", "मीरा", "structured", "hi"),
    # -- clinical values that must survive as non-identifier --
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT", "140/90", "freetext", "en", char_start=172, char_end=178),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT", "150/90", "freetext", "en", char_start=183, char_end=189),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_HISTORY", "K/C/O", "freetext", "en", char_start=67, char_end=72),
    GoldSpan("txt_notes/clinical_semantics.txt", "CLINICAL_HISTORY", "H/O", "freetext", "en", char_start=82, char_end=85),
]

GOLD_HELDOUT: list[GoldSpan] = [
    GoldSpan("cue_only_names/unseen_name_cue.txt", "PATIENT_NAME", "Zubair", "freetext", "hinglish", char_start=0, char_end=6),
    # "Smt." is a pure honorific title (excluded by design, same as "Dr." --
    # see detect.names._HONORIFIC_PREFIX_CUES), so the correct span is the
    # name alone. The previous report's "span-boundary miss" on this exact
    # case was a wrong gold expectation, not a real detector defect.
    GoldSpan("cue_only_names/unseen_name_cue.txt", "PATIENT_NAME", "Kalawati", "freetext", "en", char_start=51, char_end=59),
    GoldSpan("gazetteer_absent/cue_supported.txt", "PATIENT_NAME", "Zufishan", "freetext", "en", char_start=0, char_end=8),
    GoldSpan("gazetteer_absent/cue_supported.txt", "RELATIVE_NAME", "Qadeer Ahmed", "freetext", "en", char_start=43, char_end=55),
]

# (file_path, role) pairs that are exhaustively labelled above -- every span
# of that role actually detected in that file has a matching gold entry.
# Verified by an assertion in entity_metrics (not just claimed): if this ever
# drifts out of sync with the detector's real output, the eval run fails
# loudly instead of silently under/over-counting false positives.
FULLY_ANNOTATED_SCOPES: set[tuple[str, str]] = {
    ("csv_basic/registration.csv", "PATIENT_NAME"),
    # fhir/patient_encounter.json is deliberately excluded here: a FHIR
    # HumanName also emits name.given/name.family as their own PATIENT_NAME
    # features alongside name.text, so this file isn't exhaustively
    # single-entity-per-role the way a CSV/TXT fixture is -- recall is still
    # measured for it via GOLD_ENTITIES, just not precision/FP.
    ("arif_corroborated/registration.csv", "PATIENT_NAME"),
    ("meera_corroborated/registration.csv", "PATIENT_NAME"),
    ("arif_corroborated/opd_note.txt", "PATIENT_NAME"),
    ("arif_corroborated/discharge.txt", "PATIENT_NAME"),
    ("arif_corroborated/consultation.json", "PATIENT_NAME"),
    ("meera_corroborated/consultation.json", "PATIENT_NAME"),
    ("txt_notes/clinical_semantics.txt", "RELATIVE_NAME"),
    ("txt_notes/clinical_semantics.txt", "PROVIDER_NAME"),
    ("txt_notes/clinical_semantics.txt", "CLINICAL_HISTORY"),
    ("txt_notes/clinical_semantics.txt", "CLINICAL_MEASUREMENT"),
    ("txt_notes/provider_patient_collision.txt", "PROVIDER_NAME"),
    ("txt_notes/provider_patient_collision.txt", "PATIENT_NAME"),
    ("cue_only_names/unseen_name_cue.txt", "PATIENT_NAME"),
    ("gazetteer_absent/cue_supported.txt", "PATIENT_NAME"),
    ("gazetteer_absent/cue_supported.txt", "RELATIVE_NAME"),
    # deliberately zero gold spans: a true negative (no cue, no gazetteer
    # hit) -- any detection here would be a genuine false positive.
    ("gazetteer_absent/no_cue_no_detection.txt", "PATIENT_NAME"),
}

# Known same-patient record groups (gold clusters). A bare "file_path" spec
# means "the one record in that file"; "file_path#record_no" disambiguates
# a multi-row CSV / multi-resource FHIR file.
GOLD_CLUSTERS: dict[str, list[str]] = {
    "arif_corroborated": [
        "arif_corroborated/registration.csv", "arif_corroborated/consultation.json",
        "arif_corroborated/discharge.txt", "arif_corroborated/opd_note.txt",
    ],
    "meera_corroborated": [
        "meera_corroborated/registration.csv", "meera_corroborated/consultation.json",
        "meera_corroborated/discharge.txt",
    ],
    "source_scope_configured_shared": [
        "source_scope_configured_shared/hospitalA/records.csv",
        "source_scope_configured_shared/hospitalB/records.csv",
    ],
    "aadhaar_consistency": ["aadhaar_consistency/registration.csv", "aadhaar_consistency/note.txt"],
    "fhir_subject_pA": ["fhir_patient_link/two_patients_bundle.json#0", "fhir_patient_link/two_patients_bundle.json#1"],
    "fhir_subject_pB": ["fhir_patient_link/two_patients_bundle.json#2", "fhir_patient_link/two_patients_bundle.json#3"],
}
GOLD_NON_LINKS: list[tuple[str, str]] = [
    ("arif_corroborated/registration.csv", "arif_different/registration.csv"),
    ("arif_corroborated/registration.csv", "arif_ambiguous/opd_note_only.txt"),
    ("household_shared/family.csv#0", "household_shared/family.csv#1"),
    ("uhid_cross_issuer_hospitalA/hospitalA.csv", "uhid_cross_issuer_hospitalB/hospitalB_fhir.json"),
    ("source_scope_unknown/hospitalA/records.csv", "source_scope_unknown/hospitalB/records.csv"),
    ("source_scope_configured_distinct/hospitalA/records.csv", "source_scope_configured_distinct/hospitalB/records.csv"),
    ("conflicting_ids/same_hospital.csv#0", "conflicting_ids/same_hospital.csv#1"),
    # two distinct FHIR Patients in one Bundle: pA's records must never
    # cross-link with pB's records.
    ("fhir_patient_link/two_patients_bundle.json#0", "fhir_patient_link/two_patients_bundle.json#2"),
]


# --- record resolution (globally unique record_key, never overwritten) ------

def _resolve(records_by_key: dict[str, dict], spec: str) -> dict:
    if "#" in spec:
        path, record_no = spec.rsplit("#", 1)
        matches = [r for r in records_by_key.values() if r["file_path"] == path and r["record_key"].split(":")[1] == record_no]
    else:
        matches = [r for r in records_by_key.values() if r["file_path"] == spec]
    assert len(matches) == 1, f"gold spec {spec!r} must resolve to exactly one record, found {len(matches)}"
    return matches[0]


def _records_by_key(private: dict) -> dict[str, dict]:
    return {r["record_key"]: r for r in private["records"]}


# --- entity metrics: real TP/FP/FN, one-to-one, exact-span-coordinate -------

def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def entity_metrics(records: list[dict], gold: list[GoldSpan], fully_annotated: set[tuple[str, str]], strict: bool = True) -> dict:
    """strict=True (the real evaluation run) enforces that every
    fully_annotated (file, role) scope's gold count matches what the
    detector actually produced -- a meta-check on the GOLD DATA's own
    integrity, not the precision arithmetic. strict=False is for the
    evaluator's own synthetic unit tests (tests/test_eval_gate1.py), which
    deliberately construct a detector/gold mismatch to exercise precision
    on a false positive and must not trip this unrelated check."""
    if strict:
        for file_path, role in fully_annotated:
            gold_count = sum(1 for g in gold if g.file_path == file_path and g.role == role)
            detected_count = sum(
                1 for r in records if r["file_path"] == file_path for f in r["features"] if f["role"] == role
            )
            assert gold_count == detected_count, (
                f"FULLY_ANNOTATED_SCOPES claims {file_path}:{role} is exhaustively labelled "
                f"but gold has {gold_count} spans and detection found {detected_count} -- "
                f"fix the gold set (or the detector), not this check"
            )

    predicted = [
        {
            "file_path": r["file_path"], "record_no": int(r["record_key"].split(":")[1]),
            "role": f["role"], "char_start": f["char_start"], "char_end": f["char_end"],
            "used": False,
        }
        for r in records for f in r["features"]
    ]

    tp_exact = tp_overlap = fn = 0
    by_lang: dict[str, list[int]] = {}
    by_struct: dict[str, list[int]] = {}
    for g in gold:
        gs, ge = g.expected_span()
        exact_hit = overlap_hit = None
        for p in predicted:
            if p["used"] or p["file_path"] != g.file_path or p["record_no"] != g.record_no or p["role"] != g.role:
                continue
            if p["char_start"] == gs and p["char_end"] == ge:
                exact_hit = p
                break
            if overlap_hit is None and _overlaps(gs, ge, p["char_start"], p["char_end"]):
                overlap_hit = p
        hit = exact_hit or overlap_hit
        if hit is not None:
            hit["used"] = True
        tp_exact += 1 if exact_hit is not None else 0
        if hit is not None:
            tp_overlap += 1
        else:
            fn += 1
        by_lang.setdefault(g.lang, [0, 0])
        by_lang[g.lang][0] += 1 if hit is not None else 0
        by_lang[g.lang][1] += 1
        by_struct.setdefault(g.structure, [0, 0])
        by_struct[g.structure][0] += 1 if hit is not None else 0
        by_struct[g.structure][1] += 1

    annotated_predicted = [p for p in predicted if (p["file_path"], p["role"]) in fully_annotated]
    fp = sum(1 for p in annotated_predicted if not p["used"])
    tp_annotated = sum(1 for p in annotated_predicted if p["used"])

    total = len(gold)
    precision = tp_annotated / (tp_annotated + fp) if (tp_annotated + fp) else None
    recall_exact = tp_exact / total if total else 0.0
    recall_overlap = tp_overlap / total if total else 0.0
    f1_overlap = (
        2 * precision * recall_overlap / (precision + recall_overlap)
        if precision is not None and (precision + recall_overlap) else None
    )
    return {
        "total_gold": total,
        "true_positives_exact": tp_exact,
        "true_positives_overlap": tp_overlap,
        "false_negatives": fn,
        "false_positives_in_annotated_scope": fp,
        "precision_in_annotated_scope": round(precision, 3) if precision is not None else None,
        "recall_exact": round(recall_exact, 3),
        "recall_overlap": round(recall_overlap, 3),
        "f1_overlap": round(f1_overlap, 3) if f1_overlap is not None else None,
        "by_language": {k: round(v[0] / v[1], 3) for k, v in by_lang.items()},
        "by_structure": {k: round(v[0] / v[1], 3) for k, v in by_struct.items()},
    }


# --- real detector ablation: two independent passes, not a post-hoc filter -

def alias_vs_full_ablation(gold: list[GoldSpan]) -> dict:
    freetext_gold = [g for g in gold if g.structure == "freetext"]
    alias_only_plan = run_pipeline(DEV, EVAL_CONFIG, enabled_name_routes=frozenset({"alias"}))
    alias_only_heldout = run_pipeline(HELDOUT, EVAL_CONFIG, enabled_name_routes=frozenset({"alias"}))
    full_plan = run_pipeline(DEV, EVAL_CONFIG)
    full_heldout = run_pipeline(HELDOUT, EVAL_CONFIG)

    def recall_on(records: list[dict]) -> float:
        hits = 0
        for g in freetext_gold:
            gs, ge = g.expected_span()
            for r in records:
                if r["file_path"] != g.file_path or int(r["record_key"].split(":")[1]) != g.record_no:
                    continue
                if any(
                    f["role"] == g.role and _overlaps(gs, ge, f["char_start"], f["char_end"])
                    for f in r["features"]
                ):
                    hits += 1
                    break
        return round(hits / len(freetext_gold), 3) if freetext_gold else None

    alias_only_records = alias_only_plan.private["records"] + alias_only_heldout.private["records"]
    full_records = full_plan.private["records"] + full_heldout.private["records"]
    return {
        "note_set_size": len(freetext_gold),
        "alias_only_recall": recall_on(alias_only_records),
        "alias_gazetteer_cues_recall": recall_on(full_records),
    }


# --- link metrics: record-key indexed (never overwritten), whole-group + --
# --- pairwise precision/recall, singleton/abstention accounting ----------

def link_metrics(private: dict) -> dict:
    records_by_key = _records_by_key(private)

    def linked(spec_a: str, spec_b: str) -> bool:
        a, b = _resolve(records_by_key, spec_a), _resolve(records_by_key, spec_b)
        return a["link_status"] == "ACCEPTED" and b["link_status"] == "ACCEPTED" and a["cluster_id"] == b["cluster_id"]

    group_detail = []
    groups_fully_linked = 0
    singleton_or_held_in_groups = 0
    group_member_total = 0
    for group_name, specs in GOLD_CLUSTERS.items():
        recs = [_resolve(records_by_key, s) for s in specs]
        group_member_total += len(recs)
        singleton_or_held_in_groups += sum(1 for r in recs if r["link_status"] != "ACCEPTED")
        clusters = {r["cluster_id"] for r in recs if r["link_status"] == "ACCEPTED"}
        fully_linked = all(r["link_status"] == "ACCEPTED" for r in recs) and len(clusters) == 1 and None not in clusters
        if fully_linked:
            groups_fully_linked += 1
        group_detail.append({"group": group_name, "expected_records": len(specs), "fully_linked": fully_linked})

    incorrect_merges = 0
    merge_detail = []
    for spec_a, spec_b in GOLD_NON_LINKS:
        merged = linked(spec_a, spec_b)
        if merged:
            incorrect_merges += 1
        merge_detail.append({"a": spec_a, "b": spec_b, "incorrectly_merged": merged})

    positive_pairs = [
        (specs[i], specs[j])
        for specs in GOLD_CLUSTERS.values()
        for i in range(len(specs)) for j in range(i + 1, len(specs))
    ]
    pairwise_tp = sum(1 for a, b in positive_pairs if linked(a, b))
    pairwise_fn = len(positive_pairs) - pairwise_tp
    pairwise_fp = incorrect_merges
    pairwise_precision = pairwise_tp / (pairwise_tp + pairwise_fp) if (pairwise_tp + pairwise_fp) else None
    pairwise_recall = pairwise_tp / (pairwise_tp + pairwise_fn) if (pairwise_tp + pairwise_fn) else None

    total_groups = len(GOLD_CLUSTERS)
    return {
        "gold_same_patient_groups": total_groups,
        "groups_fully_linked": groups_fully_linked,
        "group_link_recall": round(groups_fully_linked / total_groups, 3) if total_groups else None,
        "group_detail": group_detail,
        "singletons_or_held_within_gold_groups": singleton_or_held_in_groups,
        "gold_group_member_total": group_member_total,
        "hard_negative_pairs_checked": len(GOLD_NON_LINKS),
        "incorrect_merges": incorrect_merges,
        "merge_detail": merge_detail,
        "pairwise_positive_pairs_checked": len(positive_pairs),
        "pairwise_true_positive_links": pairwise_tp,
        "pairwise_missed_links": pairwise_fn,
        "pairwise_false_merges_sampled_negatives": pairwise_fp,
        "pairwise_precision_over_sampled_negatives": round(pairwise_precision, 3) if pairwise_precision is not None else None,
        "pairwise_recall": round(pairwise_recall, 3) if pairwise_recall is not None else None,
    }


# --- candidate recall: did blocking even make the pair available? ----------

def candidate_recall(input_dir: Path, config: AppConfig) -> dict:
    """Checks the GOLD_CLUSTERS positive pairs against the actual candidate
    pool (fuzzy-blocking pairs union exact-scoped-identifier matches), BEFORE
    scoring/thresholds -- a genuinely different question from pairwise_recall
    (which measures whether the pair was actually accepted)."""
    ingest_result = run_ingest(input_dir, config)
    trusted_aliases: dict[str, FieldRole] = {}
    for doc in ingest_result.documents:
        for f in doc.fields:
            if f.role in (FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PROVIDER_NAME, FieldRole.FACILITY_NAME) \
                    and isinstance(f.value, str) and f.value.strip():
                trusted_aliases[f.value.strip()] = f.role

    record_features: dict[str, list] = {}
    record_sources: dict[str, tuple[str, ...]] = {}
    for doc in ingest_result.documents:
        record_key = f"{doc.file_no}:{doc.record_no}"
        feats = []
        if doc.txt_document is not None:
            base = SourceCoordinate(doc.file_no, doc.file_path, doc.record_no, "body", char_start=0)
            feats.extend(detect_in_text(doc.txt_document.text, base, trusted_aliases))
        else:
            for fld in doc.fields:
                feats.extend(detect_in_text(
                    fld.original_text, fld.source, trusted_aliases, structured_role=fld.role,
                    pre_issuer=fld.issuer, pre_scheme=fld.scheme,
                ))
        record_features[record_key] = feats
        record_sources[record_key] = (doc.file_path,)

    features_list = [
        build_candidate_features(k, feats, record_sources.get(k, ()), config.source_system_map)
        for k, feats in record_features.items()
    ]
    key_by_file: dict[str, list[str]] = {}
    for cf in features_list:
        key_by_file.setdefault(cf.source_files[0] if cf.source_files else "", []).append(cf.record_key)
    pairs, _truncated = generate_candidate_pairs(features_list, config.limits.max_candidate_bucket)
    candidate_set = set(pairs)
    exact_scoped: set[tuple[str, str]] = set()
    by_scope: dict[tuple[str, str, str], list[str]] = {}
    for cf in features_list:
        for ident in cf.scoped_ids:
            by_scope.setdefault((ident.issuer, ident.scheme, ident.value), []).append(cf.record_key)
    for members in by_scope.values():
        uniq = sorted(set(members))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                exact_scoped.add((uniq[i], uniq[j]) if uniq[i] < uniq[j] else (uniq[j], uniq[i]))

    def file_to_key(file_path: str) -> str | None:
        keys = key_by_file.get(file_path)
        return keys[0] if keys and len(keys) == 1 else None

    checked = 0
    found = 0
    for specs in GOLD_CLUSTERS.values():
        plain_specs = [s for s in specs if "#" not in s]
        for i in range(len(plain_specs)):
            for j in range(i + 1, len(plain_specs)):
                ka, kb = file_to_key(plain_specs[i]), file_to_key(plain_specs[j])
                if ka is None or kb is None:
                    continue
                checked += 1
                pair = (ka, kb) if ka < kb else (kb, ka)
                if pair in candidate_set or pair in exact_scoped:
                    found += 1
    return {
        "gold_positive_pairs_checked": checked,
        "pairs_available_as_candidates": found,
        "candidate_recall": round(found / checked, 3) if checked else None,
    }


def main() -> None:
    started = time.monotonic()
    dev_plan = run_pipeline(DEV, EVAL_CONFIG)
    elapsed = time.monotonic() - started
    heldout_plan = run_pipeline(HELDOUT, EVAL_CONFIG)

    report = {
        "entity_detection": entity_metrics(dev_plan.private["records"], GOLD_ENTITIES, FULLY_ANNOTATED_SCOPES),
        "entity_detection_heldout": entity_metrics(heldout_plan.private["records"], GOLD_HELDOUT, FULLY_ANNOTATED_SCOPES),
        "alias_only_vs_full_ablation": alias_vs_full_ablation(GOLD_ENTITIES + GOLD_HELDOUT),
        "linking": link_metrics(dev_plan.private),
        "candidate_recall": candidate_recall(DEV, EVAL_CONFIG),
        "dev_run_safe_report": dev_plan.safe_report,
        "dev_run_wall_seconds": round(elapsed, 4),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
