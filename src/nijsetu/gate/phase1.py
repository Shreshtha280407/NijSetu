"""Gate 1: runs real checks against the actual implementation, not a claim.

Exercises the bundled dev/held-out fixtures through the real analyze
pipeline and asserts the Gate 1 acceptance conditions from the Phase 1 spec.
Writes one evidence file per check plus a summary to --output, and returns a
nonzero process exit code if any required check fails.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..detect import detect_in_text
from ..plan import run_pipeline
from ..schema import SourceCoordinate
from .fixtures_locator import find_fixtures_root

# The synthetic dev fixtures' declared source contract: arif_corroborated/
# and meera_corroborated/ are, by fixture design, each one patient's
# fragments from the SAME hospital record system (a CSV registration, a
# JSON consultation, free-text discharge/OPD notes) -- deliberately
# configured here as sharing one issuer, exactly the "explicit source
# contract" a real deployment would supply via --source-system. Without
# this, an unconfigured hospital-local identifier (UHID) is scoped per
# file (see identity.build_candidate_features) and must NOT silently
# merge across files -- see tests/test_identity.py's source-scope cases
# for the negative side of this same contract.
DEV_SOURCE_SYSTEM_MAP: tuple[tuple[str, str], ...] = (
    ("arif_corroborated", "CITY_GENERAL_HOSPITAL"),
    ("meera_corroborated", "CITY_GENERAL_HOSPITAL"),
    # source_scope_configured_shared/*: two files deliberately declared as
    # the SAME issuer (e.g. a multi-campus network sharing one UHID scheme)
    # -- a repeated local identifier under this explicit contract IS a
    # legitimate exact link (positive case for Finding 1's required fix).
    ("source_scope_configured_shared/hospitalA", "NETWORK_X"),
    ("source_scope_configured_shared/hospitalB", "NETWORK_X"),
    # source_scope_configured_distinct/*: two files deliberately declared as
    # two DIFFERENT issuers -- a repeated local identifier under two
    # configured, distinct issuers must still never merge.
    ("source_scope_configured_distinct/hospitalA", "DISTINCT_ISSUER_A"),
    ("source_scope_configured_distinct/hospitalB", "DISTINCT_ISSUER_B"),
    # source_scope_unknown/* is intentionally left unconfigured: it is the
    # direct reproduction of the original bug report (two independently
    # sourced, unconfigured files sharing a local id must not auto-merge).
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)


def _records_of(private: dict, path_substring: str) -> list[dict]:
    """ALL records matching the path, regardless of link status -- the base
    every check below starts from, so a missing fixture or a group that
    silently lost records to HELD/SINGLETON is visible, not hidden behind
    an accepted-only filter."""
    return [r for r in private["records"] if path_substring in r["file_path"]]


def _cluster_of(private: dict, path_substring: str) -> set[str | None]:
    return {r["cluster_id"] for r in _records_of(private, path_substring) if r["link_status"] == "ACCEPTED"}


def _record_at(private: dict, file_path: str, record_no: int) -> dict | None:
    for r in private["records"]:
        if r["file_path"] == file_path and r["record_key"].split(":")[1] == str(record_no):
            return r
    return None


def _fully_linked_group(private: dict, path_substring: str, expected_count: int) -> tuple[bool, str]:
    """A group is fully linked only when it has exactly the expected number
    of records, every one of them is ACCEPTED, and they all share one
    cluster id -- not merely "the accepted ones agree" (which an empty or
    partial accepted set would satisfy vacuously)."""
    recs = _records_of(private, path_substring)
    if len(recs) != expected_count:
        return False, f"expected {expected_count} records for {path_substring!r}, found {len(recs)}"
    if not all(r["link_status"] == "ACCEPTED" for r in recs):
        statuses = [r["link_status"] for r in recs]
        return False, f"not all records ACCEPTED for {path_substring!r}: {statuses}"
    clusters = {r["cluster_id"] for r in recs}
    if len(clusters) != 1 or None in clusters:
        return False, f"records for {path_substring!r} did not converge on one cluster: {clusters}"
    return True, f"{expected_count}/{expected_count} ACCEPTED into one cluster"


def _reasons_for(private: dict, path_substring: str) -> list[str]:
    out = []
    for r in private["records"]:
        if path_substring in r["file_path"]:
            out.extend(r["reason_codes"])
    return out


def _names_in(private: dict, path_substring: str) -> list[str]:
    out = []
    for r in private["records"]:
        if path_substring in r["file_path"]:
            out.extend(f["original_text"] for f in r["features"] if f["role"] == "PATIENT_NAME")
    return out


def _check_adapters_and_encodings(private: dict) -> CheckResult:
    by_path = {fr["file_path"]: fr for fr in private["file_records"]}
    expected_processed = [
        "encodings/strict_utf8.csv", "encodings/bom_utf8.csv", "encodings/bom_utf16.csv",
        "csv_basic/registration.csv", "csv_basic/tsv_sample.tsv",
        "json_basic/object_record.json", "json_basic/array_records.json",
        "txt_notes/clinical_semantics.txt",
        "fhir/patient_encounter.json", "fhir/bundle_collection.json",
    ]
    missing = [p for p in expected_processed if by_path.get(p, {}).get("disposition") != "PROCESSED"]
    return CheckResult(
        "adapters_and_encodings", not missing,
        "all declared adapters/encodings processed" if not missing else f"not processed as expected: {missing}",
        {"checked": expected_processed},
    )


def _check_malformed_safe_reasons(private: dict) -> CheckResult:
    by_path = {fr["file_path"]: fr for fr in private["file_records"]}
    expectations = {
        "csv_malformed/duplicate_headers.csv": ("HELD", "DUPLICATE_HEADER"),
        "json_malformed/duplicate_key.json": ("QUARANTINED", "DUPLICATE_OBJECT_KEY"),
        "json_malformed/syntax_error.json": ("QUARANTINED", "JSON_SYNTAX_ERROR"),
        "unsupported/scan.pdf": ("UNSUPPORTED", "UNSUPPORTED_EXTENSION"),
    }
    problems = []
    for path, (disposition, reason) in expectations.items():
        fr = by_path.get(path)
        if fr is None or fr["disposition"] != disposition or reason not in fr["reason_codes"]:
            problems.append((path, disposition, reason, fr))
    return CheckResult(
        "malformed_inputs_safe_reasons", not problems,
        "malformed/unsupported fixtures carry explicit safe reasons" if not problems else f"mismatches: {problems}",
    )


def _check_unanchored_names(private: dict, heldout_private: dict) -> CheckResult:
    arif_bare = "Arif" in _names_in(private, "arif_corroborated/opd_note.txt")
    zubair = any("Zubair" in n for n in _names_in(heldout_private, "cue_only_names/unseen_name_cue.txt"))
    kalawati = any("Kalawati" in n for n in _names_in(heldout_private, "cue_only_names/unseen_name_cue.txt"))
    zufishan = any("Zufishan" in n for n in _names_in(heldout_private, "gazetteer_absent/cue_supported.txt"))
    ok = arif_bare and zubair and kalawati and zufishan
    return CheckResult(
        "unanchored_cue_names_detected", ok,
        f"arif_bare={arif_bare} zubair={zubair} kalawati={kalawati} zufishan={zufishan}",
    )


def _check_corroborated_links(private: dict) -> CheckResult:
    arif_ok, arif_detail = _fully_linked_group(private, "arif_corroborated/", 4)
    meera_ok, meera_detail = _fully_linked_group(private, "meera_corroborated/", 3)
    ok = arif_ok and meera_ok
    return CheckResult("corroborated_links", ok, f"arif: {arif_detail}; meera: {meera_detail}")


def _require_records(private: dict, path_substring: str, expected_count: int) -> tuple[bool, str]:
    """A hard-negative fixture that silently disappeared (missing/renamed
    file, zero records) must fail its check, not pass vacuously because an
    empty set has no intersection with anything."""
    recs = _records_of(private, path_substring)
    ok = len(recs) == expected_count
    return ok, f"{path_substring!r}: expected {expected_count} records, found {len(recs)}"


def _check_hard_negatives(private: dict) -> CheckResult:
    problems = []

    presence_checks = [
        ("arif_corroborated/", 4), ("arif_different/", 1), ("household_shared/", 2),
        ("uhid_cross_issuer_hospitalA/", 1), ("uhid_cross_issuer_hospitalB/", 1),
        ("conflicting_ids/", 2), ("bridge_conflict/", 3),
        ("source_scope_unknown/hospitalA/", 1), ("source_scope_unknown/hospitalB/", 1),
        ("source_scope_configured_distinct/hospitalA/", 1), ("source_scope_configured_distinct/hospitalB/", 1),
    ]
    for path_substring, expected in presence_checks:
        ok, detail = _require_records(private, path_substring, expected)
        if not ok:
            problems.append(f"MISSING FIXTURE DATA: {detail}")

    if problems:
        # Can't meaningfully evaluate merge behavior over fixtures that
        # aren't even present -- fail loudly rather than silently scoring
        # an empty comparison as "no incorrect merges".
        return CheckResult("hard_negatives_zero_incorrect_merges", False, "; ".join(problems))

    arif_corrob = _cluster_of(private, "arif_corroborated/")
    arif_diff = _cluster_of(private, "arif_different/")
    household = _cluster_of(private, "household_shared/")
    uhid_a = _cluster_of(private, "uhid_cross_issuer_hospitalA/")
    uhid_b = _cluster_of(private, "uhid_cross_issuer_hospitalB/")
    conflicting = _cluster_of(private, "conflicting_ids/")
    bridge_all_clusters = {r["cluster_id"] for r in _records_of(private, "bridge_conflict/")}
    scope_unknown_a = _cluster_of(private, "source_scope_unknown/hospitalA/")
    scope_unknown_b = _cluster_of(private, "source_scope_unknown/hospitalB/")
    scope_distinct_a = _cluster_of(private, "source_scope_configured_distinct/hospitalA/")
    scope_distinct_b = _cluster_of(private, "source_scope_configured_distinct/hospitalB/")

    if arif_diff & arif_corrob:
        problems.append("arif_different merged into arif_corroborated")
    if len(household) > 0:
        problems.append("household_shared members were merged")
    if uhid_a & uhid_b:
        problems.append("uhid_cross_issuer merged across issuers")
    if len(conflicting) > 0:
        problems.append("conflicting_ids members were merged")
    if len(bridge_all_clusters) == 1:
        problems.append("bridge_conflict fully merged into one cluster")
    if scope_unknown_a & scope_unknown_b:
        problems.append("source_scope_unknown merged across unconfigured, independently sourced files")
    if scope_distinct_a & scope_distinct_b:
        problems.append("source_scope_configured_distinct merged across two deliberately distinct issuers")

    return CheckResult("hard_negatives_zero_incorrect_merges", not problems, "; ".join(problems) or "no incorrect merges")


def _check_source_scope_regression(private: dict) -> CheckResult:
    """Finding 1: explicit --source-system scoping, both the positive
    (deliberately shared issuer legitimately links) and negative
    (configured-distinct / unconfigured never merge) sides, plus the
    national-scheme-stays-consistent-across-routes positive case."""
    shared_ok, shared_detail = _fully_linked_group(private, "source_scope_configured_shared/", 2)
    aadhaar_ok, aadhaar_detail = _fully_linked_group(private, "aadhaar_consistency/", 2)
    problems = []
    if not shared_ok:
        problems.append(f"configured_shared should legitimately link: {shared_detail}")
    if not aadhaar_ok:
        problems.append(f"aadhaar_consistency should legitimately link: {aadhaar_detail}")
    return CheckResult("source_scope_regression", not problems, "; ".join(problems) or "configured-shared and national-scheme links both hold")


def _check_fhir_subject_reference_regression(private: dict) -> CheckResult:
    """Finding 2: subject.reference establishes patient association;
    performer/ambiguous/duplicate-logical-id never do."""
    two_patients = _records_of(private, "fhir_patient_link/two_patients_bundle.json")
    performer = _records_of(private, "fhir_patient_link/performer_not_patient.json")
    ambiguous = _records_of(private, "fhir_patient_link/ambiguous_subject_bundle.json")
    duplicate_id = _records_of(private, "fhir_patient_link/duplicate_logical_id_bundle.json")

    problems = []
    if len(two_patients) != 4 or len(performer) != 2 or len(ambiguous) != 3 or len(duplicate_id) != 3:
        problems.append(
            f"missing FHIR patient-link fixture data: two_patients={len(two_patients)} "
            f"performer={len(performer)} ambiguous={len(ambiguous)} duplicate_id={len(duplicate_id)}"
        )
        return CheckResult("fhir_subject_reference_regression", False, "; ".join(problems))

    ordered = sorted(two_patients, key=lambda r: int(r["record_key"].split(":")[1]))
    pA, eA, pB, oB = ordered
    if not (eA["link_status"] == "ACCEPTED" and eA["cluster_id"] == pA["cluster_id"]):
        problems.append("Encounter did not join its referenced Patient's cluster")
    if not (oB["link_status"] == "ACCEPTED" and oB["cluster_id"] == pB["cluster_id"]):
        problems.append("Observation did not join its referenced Patient's cluster")
    if pA["cluster_id"] == pB["cluster_id"]:
        problems.append("two distinct Patients were cross-linked")

    practitioner, observation = sorted(performer, key=lambda r: int(r["record_key"].split(":")[1]))
    if observation["cluster_id"] == practitioner["cluster_id"]:
        problems.append("a performer/Practitioner reference caused a patient-identity merge")

    amb_sorted = sorted(ambiguous, key=lambda r: int(r["record_key"].split(":")[1]))
    obs_amb = amb_sorted[2]
    if obs_amb["link_status"] != "HELD" or "FHIR_SUBJECT_REFERENCE_AMBIGUOUS" not in obs_amb["reason_codes"]:
        problems.append("an ambiguous subject reference was not held with an explicit reason")
    if obs_amb["cluster_id"] in (amb_sorted[0]["cluster_id"], amb_sorted[1]["cluster_id"]):
        problems.append("an ambiguous subject reference resolved arbitrarily into one of its candidate Patients")

    dup_sorted = sorted(duplicate_id, key=lambda r: int(r["record_key"].split(":")[1]))
    alpha, beta, enc = dup_sorted
    if enc["cluster_id"] in (alpha["cluster_id"], beta["cluster_id"]):
        problems.append("a duplicate logical id resolved arbitrarily into one of the colliding Patients")

    return CheckResult(
        "fhir_subject_reference_regression", not problems,
        "; ".join(problems) or "subject-reference patient association holds in every regression case",
    )


def _check_fhir_contained_scope_regression(fixtures_root: Path) -> CheckResult:
    """Finding 3: #contained references resolve only within their own
    owning resource -- exercised directly against the adapter (parse_fhir),
    since Phase 1 does not extract contained resources into identity
    evidence and so this is not independently observable at the identity
    layer."""
    from ..ingest.fhir_adapter import parse_fhir
    from ..config import Limits

    path = fixtures_root / "dev" / "fhir_contained_scope" / "owner_scoped_contained.json"
    if not path.is_file():
        return CheckResult("fhir_contained_scope_regression", False, f"fixture not found: {path}")
    result = parse_fhir(path.read_bytes(), "fhir_contained_scope/owner_scoped_contained.json", Limits())
    by_id = {r.logical_id: r for r in result.resources}
    expected_ids = {"encOwnerA", "obsOther", "encDupContained", "encMissing", "encOwnerB"}
    if by_id.keys() != expected_ids:
        return CheckResult("fhir_contained_scope_regression", False, f"unexpected resource set: {sorted(by_id)}")

    problems = []
    if by_id["encOwnerA"].references[0].resolved is not True:
        problems.append("valid same-owner contained reference did not resolve")
    if by_id["obsOther"].references[0].resolved is not False:
        problems.append("a different owner's contained id resolved across owners")
    if by_id["encDupContained"].references[0].resolved is not False:
        problems.append("a duplicate contained id within one owner resolved anyway")
    if by_id["encMissing"].references[0].resolved is not False:
        problems.append("a missing contained target resolved anyway")
    if by_id["encOwnerB"].references[0].resolved is not True:
        problems.append("a reused contained id under its own (second) owner failed to resolve independently")

    return CheckResult(
        "fhir_contained_scope_regression", not problems,
        "; ".join(problems) or "contained references resolve correctly within owner scope",
    )


def _check_fhir_unsupported_entries_regression(private: dict) -> CheckResult:
    """Finding 4: unsupported/malformed Bundle entries are held explicitly,
    never silently dropped into a falsely-clean PROCESSED/0-resources file."""
    by_path = {fr["file_path"]: fr for fr in private["file_records"]}
    held_by_file: dict[str, list] = {}
    for h in private["held_records"]:
        held_by_file.setdefault(h["file_path"], []).append(h)

    problems = []
    unsupported_only = by_path.get("fhir_unsupported_entries/unsupported_only_bundle.json")
    if unsupported_only is None or unsupported_only["disposition"] != "HELD" \
            or "FHIR_NO_SUPPORTED_RESOURCES_IN_BUNDLE" not in unsupported_only["reason_codes"]:
        problems.append(f"unsupported-only bundle not explicitly held: {unsupported_only}")

    mixed = by_path.get("fhir_unsupported_entries/mixed_supported_unsupported_bundle.json")
    mixed_held = held_by_file.get("fhir_unsupported_entries/mixed_supported_unsupported_bundle.json", [])
    if mixed is None or mixed["disposition"] != "PROCESSED" or mixed["record_count"] != 1:
        problems.append(f"mixed bundle's supported entry was not processed cleanly: {mixed}")
    if len(mixed_held) != 4:
        problems.append(f"mixed bundle's unsupported/malformed entries were not all held explicitly: {mixed_held}")
    elif any(not h["reason_codes"] for h in mixed_held):
        problems.append("a held FHIR bundle entry carries no reason code")

    empty = by_path.get("fhir_unsupported_entries/supported_empty_bundle.json")
    if empty is None or empty["disposition"] != "PROCESSED" or empty["reason_codes"]:
        problems.append(f"a genuinely empty Bundle was not cleanly processed: {empty}")

    return CheckResult(
        "fhir_unsupported_entries_regression", not problems,
        "; ".join(problems) or "unsupported/malformed Bundle entries are never silent",
    )


def _check_explicit_conflict_reasons(private: dict) -> CheckResult:
    conflicting_reasons = _reasons_for(private, "conflicting_ids/")
    ambiguous_reasons = _reasons_for(private, "arif_ambiguous/")
    ok = "ID_CONFLICT" in conflicting_reasons and len(ambiguous_reasons) > 0 and ambiguous_reasons != ["NO_CORROBORATING_EVIDENCE"]
    return CheckResult(
        "conflicts_and_ambiguity_explicit_reasons", ok,
        f"conflicting={conflicting_reasons} ambiguous={ambiguous_reasons}",
    )


def _check_clinical_semantics() -> CheckResult:
    base = SourceCoordinate(file_no=0, file_path="x.txt", record_no=0, path="body")
    cases = {
        "Address: C/O Ramesh, Ward 5": ("RELATIVE_NAME", "Ramesh"),
        "OPD: C/O fever for 5 days": ("CLINICAL_HISTORY", None),
        "K/C/O diabetes": ("CLINICAL_HISTORY", None),
        "H/O hypertension": ("CLINICAL_HISTORY", None),
        "Dr. Ramesh advised rest.": ("PROVIDER_NAME", "Ramesh"),
        "Ramipril 5 mg twice daily": ("CLINICAL_MEASUREMENT", None),
        "BP 140/90 and 150/90": ("CLINICAL_MEASUREMENT", None),
        "HbA1c 8.2%": ("CLINICAL_MEASUREMENT", None),
    }
    problems = []
    for text, (expect_role, expect_text) in cases.items():
        feats = detect_in_text(text, base)
        roles = [f.role.value for f in feats]
        if expect_role not in roles:
            problems.append(f"{text!r} missing role {expect_role}: got {roles}")
            continue
        if expect_text is not None:
            matched = any(f.role.value == expect_role and f.original_text == expect_text for f in feats)
            if not matched:
                problems.append(f"{text!r} expected text {expect_text!r} under {expect_role}")
    # Dr. Ramesh must NOT also be flagged as a bare patient/relative name
    dr_feats = detect_in_text("Dr. Ramesh advised rest.", base)
    if any(f.role.value in ("PATIENT_NAME", "RELATIVE_NAME") for f in dr_feats):
        problems.append("Dr. Ramesh incorrectly also flagged as patient/relative name")
    return CheckResult("clinical_semantics", not problems, "; ".join(problems) or "all clinical semantics pass")


_CANARY_TEXT = "ZZCANARYPATIENT9988776655"
_FILENAME_CANARY_TEXT = "SYNTHETIC_CANARY_PATIENT_9988776655"


def _check_no_canary_leak(safe_report: dict, private_path: Path, filename_canary_safe_report: dict) -> CheckResult:
    """Two canaries: a value planted inside a field (the original check),
    and -- the regression this guards -- a canary planted in a raw
    FILENAME, reached only through an exact scoped-identifier reason code
    (identity.scoped._public_scope_label) when the match comes from the
    conservative per-file UNKNOWN_SOURCE fallback (Finding 1)."""
    report_text = json.dumps(safe_report)
    leaked_in_report = _CANARY_TEXT in report_text
    filename_report_text = json.dumps(filename_canary_safe_report)
    leaked_filename = _FILENAME_CANARY_TEXT in filename_report_text
    ok = not leaked_in_report and not leaked_filename
    detail = []
    if leaked_in_report:
        detail.append("CONTENT CANARY LEAKED INTO SAFE REPORT")
    if leaked_filename:
        detail.append("FILENAME CANARY LEAKED INTO SAFE REPORT (via a reason code)")
    return CheckResult("no_canary_leak_in_safe_report", ok, "; ".join(detail) or "canaries absent from safe report")


def _check_private_artifact_protected(private_path: Path) -> CheckResult:
    mode = stat.S_IMODE(os.stat(private_path).st_mode)
    owner_only = mode == (stat.S_IRUSR | stat.S_IWUSR)
    labelled = "PRIVATE" in json.loads(private_path.read_text(encoding="utf-8")).get("label", "")
    ok = owner_only and labelled
    return CheckResult(
        "private_artifact_protected", ok,
        f"mode={oct(mode)} labelled={labelled}",
    )


def _check_wheel_offline_gazetteer(repo_root: Path) -> CheckResult:
    vendor_wheels = repo_root / "vendor" / "wheels"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_out = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", str(repo_root), "--no-deps", "-w", str(tmp_path)],
            capture_output=True, text=True,
        )
        wheels = list(tmp_path.glob("nijsetu-*.whl"))
        if build_out.returncode != 0 or not wheels:
            return CheckResult("wheel_offline_gazetteer", False, f"wheel build failed: {build_out.stderr[-2000:]}")

        venv_dir = tmp_path / "venv"
        venv_out = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True)
        if venv_out.returncode != 0:
            return CheckResult("wheel_offline_gazetteer", False, f"venv creation failed: {venv_out.stderr[-2000:]}")
        venv_python = venv_dir / "bin" / "python"

        # --no-index: the pinned runtime dependency (rapidfuzz) must resolve
        # entirely from the local vendor wheelhouse, proving no network call
        # is required to install or run the package.
        install_out = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--no-index",
             "--find-links", str(tmp_path), "--find-links", str(vendor_wheels),
             str(wheels[0])],
            capture_output=True, text=True,
        )
        if install_out.returncode != 0:
            return CheckResult("wheel_offline_gazetteer", False, f"offline install failed: {install_out.stderr[-2000:]}")

        probe = subprocess.run(
            [str(venv_python), "-c", "from nijsetu.gazetteer import is_known_first_name; "
                                      "assert is_known_first_name('Meera'); print('GAZETTEER_OK')"],
            capture_output=True, text=True,
        )
        gazetteer_ok = probe.returncode == 0 and "GAZETTEER_OK" in probe.stdout

        cli_input = tmp_path / "cli_smoke_in"
        cli_input.mkdir()
        (cli_input / "tiny.csv").write_text("Patient Name,UHID\nMeera Sharma,UH1\n", encoding="utf-8")
        cli_out = subprocess.run(
            [str(venv_python), "-m", "nijsetu", "analyze", "--input", str(cli_input),
             "--output", str(tmp_path / "cli_smoke_out")],
            capture_output=True, text=True,
        )
        cli_ok = cli_out.returncode in (0, 2)

        ok = gazetteer_ok and cli_ok
        return CheckResult(
            "wheel_offline_gazetteer", ok,
            f"gazetteer_ok={gazetteer_ok} cli_ok={cli_ok} cli_rc={cli_out.returncode} "
            f"probe_stderr={probe.stderr[-300:]} cli_stderr={cli_out.stderr[-300:]}",
        )


def run_gate_phase1(output_dir: Path) -> int:
    if output_dir.exists() and any(output_dir.iterdir()):
        print(json.dumps({"status": "ERROR", "reason_code": "OUTPUT_DIR_NOT_EMPTY"}), file=sys.stderr)
        return 4
    output_dir.mkdir(parents=True, exist_ok=True)

    fixtures_root = find_fixtures_root()
    if fixtures_root is None:
        (output_dir / "summary.json").write_text(json.dumps({
            "status": "BLOCKED", "reason_code": "FIXTURES_NOT_FOUND",
        }, indent=2))
        print(json.dumps({"status": "BLOCKED", "reason_code": "FIXTURES_NOT_FOUND"}), file=sys.stderr)
        return 3

    config = AppConfig(source_system_map=DEV_SOURCE_SYSTEM_MAP)
    dev_dir = fixtures_root / "dev"
    heldout_dir = fixtures_root / "heldout"

    # Canary: an unlabelled synthetic identifier that must never surface in the safe report.
    canary_dir = output_dir / "_canary_input"
    canary_dir.mkdir(parents=True, exist_ok=True)
    (canary_dir / "canary.csv").write_text(f"Patient Name,UHID,Phone\n{_CANARY_TEXT},UHCANARY,9999999999\n", encoding="utf-8")

    # Second canary: the identifying text lives only in the FILENAME, and is
    # reached only by two matching, unconfigured UHID rows forcing an exact
    # scoped-identifier reason code through the per-file fallback path.
    filename_canary_dir = output_dir / "_filename_canary_input"
    filename_canary_dir.mkdir(parents=True, exist_ok=True)
    (filename_canary_dir / f"{_FILENAME_CANARY_TEXT}.csv").write_text(
        "Patient Name,UHID,Phone\nAsha Rao,UHCANARY2,9999999991\nNeha Rao,UHCANARY2,9999999992\n", encoding="utf-8",
    )

    dev_plan = run_pipeline(dev_dir, config)
    heldout_plan = run_pipeline(heldout_dir, config)
    canary_plan = run_pipeline(canary_dir, config)
    filename_canary_plan = run_pipeline(filename_canary_dir, AppConfig())

    dev_out = output_dir / "dev_run"
    from ..plan import write_plan
    write_plan(dev_plan, dev_out)

    checks = [
        _check_adapters_and_encodings(dev_plan.private),
        _check_malformed_safe_reasons(dev_plan.private),
        _check_unanchored_names(dev_plan.private, heldout_plan.private),
        _check_corroborated_links(dev_plan.private),
        _check_hard_negatives(dev_plan.private),
        _check_source_scope_regression(dev_plan.private),
        _check_fhir_subject_reference_regression(dev_plan.private),
        _check_fhir_contained_scope_regression(fixtures_root),
        _check_fhir_unsupported_entries_regression(dev_plan.private),
        _check_explicit_conflict_reasons(dev_plan.private),
        _check_clinical_semantics(),
        _check_no_canary_leak(canary_plan.safe_report, dev_out / "private" / "analysis.json", filename_canary_plan.safe_report),
        _check_private_artifact_protected(dev_out / "private" / "analysis.json"),
        _check_wheel_offline_gazetteer(fixtures_root.parent.parent),
    ]

    for check in checks:
        (output_dir / f"{check.name}.json").write_text(json.dumps({
            "name": check.name, "passed": check.passed, "detail": check.detail, "evidence": check.evidence,
        }, indent=2), encoding="utf-8")

    all_passed = all(c.passed for c in checks)
    summary = {
        "status": "PASS" if all_passed else "FAIL",
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all_passed else 2
