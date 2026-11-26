"""Gate 2: runs real checks against the actual Phase 2 implementation.

Exercises the bundled tests/fixtures/phase2/dev fixtures through the real
sanitize/validate pipeline and asserts the Gate 2 acceptance conditions.
Writes one evidence file per check plus a summary to --output, and returns a
nonzero process exit code if any required check fails.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..phase2.policy import BALANCED
from ..phase2.sanitize import run_sanitize
from ..phase2.validate import validate_release
from .fixtures_locator import find_fixtures_root

_MASTER_KEY = b"gate-phase2-master-secret-key-material-32bytes!"

# core_patient/ is, by fixture design, one hospital's own mixed-format
# export of one patient (same contract as Phase 1's DEV_SOURCE_SYSTEM_MAP) --
# an unconfigured hospital-local identifier (UHID) is scoped per FILE by
# default and must not silently merge a patient's CSV/JSON/TXT fragments
# without this explicit declaration. Each check below sanitizes core_patient/
# directly (not the whole fixtures_dir), so the map uses bare filenames.
PHASE2_SOURCE_SYSTEM_MAP: tuple[tuple[str, str], ...] = (
    ("registration.csv", "CORE_HOSPITAL"),
    ("consultation.json", "CORE_HOSPITAL"),
    ("discharge.txt", "CORE_HOSPITAL"),
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)


def _fixtures_dir() -> Path | None:
    root = find_fixtures_root()
    if root is None:
        return None
    candidate = root / "phase2" / "dev"
    return candidate if candidate.is_dir() else None


def _sanitize(input_dir: Path, out_dir: Path, release_id: str = "GATE2", policy=BALANCED, config: AppConfig | None = None):
    return run_sanitize(input_dir, out_dir, policy, release_id, _MASTER_KEY,
                         config or AppConfig(source_system_map=PHASE2_SOURCE_SYSTEM_MAP))


def _read_released(out_dir: Path) -> str:
    text = []
    released = out_dir / "released"
    if released.is_dir():
        for p in sorted(released.rglob("*")):
            if p.is_file():
                text.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(text)


def _check_mixed_batch_valid_release(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "mixed_out"
    outcome = _sanitize(fixtures_dir, out)
    ok = outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS") and outcome.report["counts"]["records_released"] > 0
    validation = validate_release(out) if ok else None
    ok = ok and validation is not None and validation.passed
    return CheckResult("mixed_batch_valid_release", ok,
                        f"status={outcome.status} records_released={outcome.report.get('counts', {}).get('records_released')} "
                        f"validate_passed={validation.passed if validation else None}")


def _check_direct_identifiers_removed(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "direct_id_out"
    _sanitize(fixtures_dir / "core_patient", out)
    content = _read_released(out) + (out / "report.json").read_text(encoding="utf-8") + (out / "manifest.json").read_text(encoding="utf-8")
    leaks = [lit for lit in ("Mohammad Arif", "AR001", "9876543210", "Ramesh") if lit in content]
    # "Ramesh" is a C/O relative name, masked; the clinical "Ward 5"/"Lucknow" is fine to appear (coarse geography).
    return CheckResult("direct_identifiers_removed", not leaks, f"leaked={leaks}" if leaks else "no direct identifiers found")


def _check_consistent_pseudonyms(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "consistent_out"
    _sanitize(fixtures_dir / "core_patient", out)
    content = _read_released(out)
    tokens = set(re.findall(r"TKN-UHID-[a-z0-9]+", content))
    ok = len(tokens) == 1
    return CheckResult("consistent_pseudonyms_across_formats", ok, f"tokens found={tokens}")


def _check_release_rotation(fixtures_dir: Path, work: Path) -> CheckResult:
    out_a = work / "rot_a"
    out_b = work / "rot_b"
    _sanitize(fixtures_dir / "core_patient", out_a, release_id="RELEASE_A")
    _sanitize(fixtures_dir / "core_patient", out_b, release_id="RELEASE_B")
    tokens_a = set(re.findall(r"TKN-UHID-[a-z0-9]+", _read_released(out_a)))
    tokens_b = set(re.findall(r"TKN-UHID-[a-z0-9]+", _read_released(out_b)))
    ok = bool(tokens_a) and bool(tokens_b) and tokens_a.isdisjoint(tokens_b)
    return CheckResult("different_release_rotates_tokens", ok, f"a={tokens_a} b={tokens_b}")


def _check_replay_determinism(fixtures_dir: Path, work: Path) -> CheckResult:
    out_1 = work / "replay_1"
    out_2 = work / "replay_2"
    _sanitize(fixtures_dir / "core_patient", out_1, release_id="REPLAY")
    _sanitize(fixtures_dir / "core_patient", out_2, release_id="REPLAY")
    files_1 = sorted((out_1 / "released").rglob("*"))
    files_2 = sorted((out_2 / "released").rglob("*"))
    names_match = [p.name for p in files_1] == [p.name for p in files_2]
    content_match = names_match and all(
        p1.read_bytes() == p2.read_bytes() for p1, p2 in zip(files_1, files_2) if p1.is_file()
    )
    return CheckResult("same_release_replay_preserves_content", content_match, f"names_match={names_match} content_match={content_match}")


def _check_clinical_measurements_survive(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "clinical_out"
    _sanitize(fixtures_dir / "core_patient", out)
    content = _read_released(out)
    checks = {
        "BP 140/90": "140/90" in content,
        "HbA1c 8.2%": "8.2%" in content,
        "Ramipril 5 mg": "Ramipril 5 mg" in content and "twice daily" in content,
    }
    date_pairs = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", content)
    duration_ok = True  # duration verified structurally below if both dates found
    import datetime
    dates = sorted({datetime.date.fromisoformat(d) for d in date_pairs})
    if len(dates) >= 2:
        duration_ok = (dates[-1] - dates[0]).days == 5
    ok = all(checks.values()) and duration_ok
    detail = f"{checks} duration_ok={duration_ok} dates={sorted(str(d) for d in dates)}"
    return CheckResult("clinical_measurements_and_interval_survive", ok, detail)


def _check_fhir_references_resolve(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "fhir_out"
    outcome = _sanitize(fixtures_dir / "fhir_bundle", out)
    released = out / "released"
    problems = []
    for p in released.rglob("*.json") if released.is_dir() else []:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("resourceType") != "Bundle":
            continue
        ids = {e["resource"]["id"] for e in data.get("entry", []) if e.get("resource", {}).get("id")}
        for e in data.get("entry", []):
            res = e.get("resource", {})
            for key in ("subject", "encounter"):
                ref = res.get(key, {}).get("reference", "")
                if ref.startswith("urn:uuid:") and ref[len("urn:uuid:"):] not in ids:
                    problems.append(f"{key} unresolved in {res.get('resourceType')}")
    ok = not problems and outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
    return CheckResult("fhir_required_references_resolve", ok, "; ".join(problems) or f"status={outcome.status}, all references resolve")


def _check_small_cohort_retained(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "small_cohort_out"
    outcome = _sanitize(fixtures_dir / "small_cohort", out)
    released = sum(1 for p in (out / "released").glob("*") if p.is_file()) if (out / "released").is_dir() else 0
    ok = outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS") and released == 3 and outcome.report["counts"]["records_held"] == 0
    return CheckResult("small_cohort_policy_retains_records", ok,
                        f"status={outcome.status} files_released={released} records_held={outcome.report['counts']['records_held']}")


def _check_unresolved_standalone(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "standalone_out"
    outcome = _sanitize(fixtures_dir / "unresolved_standalone", out)
    ok = outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS") and outcome.report["counts"]["records_released"] == 1
    return CheckResult("unresolved_identity_still_sanitized_standalone", ok, f"status={outcome.status} counts={outcome.report.get('counts')}")


def _check_malformed_unsupported_holds(fixtures_dir: Path, work: Path) -> CheckResult:
    # Both fixtures here are entirely unsupported/malformed, so the whole
    # batch legitimately BLOCKs (no record survives) -- the hold reasons are
    # still exposed as a safe diagnostic summary in the report either way.
    out = work / "malformed_out"
    outcome = _sanitize(fixtures_dir / "malformed_and_unsupported", out)
    reasons = set(outcome.report.get("hold_summary", {}))
    ok = "DUPLICATE_HEADER" in reasons and ("UNSUPPORTED_EXTENSION" in reasons or "UNKNOWN_FORMAT" in reasons)
    return CheckResult("unsupported_malformed_explicit_holds", ok, f"status={outcome.status} reasons={reasons}")


def _check_filename_leak_blocked(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "leak_out"
    outcome = _sanitize(fixtures_dir / "leak_canary", out)
    canary = "SYNTHETIC_PHASE2_CANARY_8877665544"
    leaked = canary in json.dumps(outcome.report)
    if outcome.output_dir is not None:
        for p in outcome.output_dir.rglob("*"):
            if canary in str(p.relative_to(outcome.output_dir)):
                leaked = True
            if p.is_file() and canary in p.read_text(encoding="utf-8", errors="ignore"):
                leaked = True
    ok = not leaked
    return CheckResult("filename_canary_never_leaks", ok, f"status={outcome.status} leaked={leaked}")


def _check_fault_injection_no_partial_release(fixtures_dir: Path, work: Path) -> CheckResult:
    """Forces a write failure after staging but before promotion -- the
    output directory must not exist/contain a new release afterward."""
    import nijsetu.phase2.staging as staging_mod

    out = work / "fault_out"
    original_publish = staging_mod.Staging.publish

    def _boom(self):
        raise staging_mod.StagingError("INJECTED_WRITE_FAILURE")

    staging_mod.Staging.publish = _boom
    try:
        outcome = _sanitize(fixtures_dir / "core_patient", out, release_id="FAULT")
    finally:
        staging_mod.Staging.publish = original_publish
    ok = outcome.status == "BLOCKED" and not out.exists()
    return CheckResult("forced_write_failure_leaves_no_release", ok, f"status={outcome.status} output_exists={out.exists()}")


def _check_manifest_tampering_detected(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "tamper_out"
    _sanitize(fixtures_dir / "core_patient", out, release_id="TAMPER")
    released_files = sorted((out / "released").glob("*"))
    if released_files:
        p = released_files[0]
        p.write_text(p.read_text(encoding="utf-8") + "\ntampered,row,here\n", encoding="utf-8")
    result = validate_release(out)
    tamper_check = next((c for c in result.checks if c["name"] == "manifest_integrity"), None)
    ok = tamper_check is not None and not tamper_check["passed"]
    return CheckResult("manifest_tampering_detected_by_validate", ok, f"{tamper_check}")


def _check_private_artifacts_never_public(fixtures_dir: Path, work: Path) -> CheckResult:
    out = work / "private_out"
    _sanitize(fixtures_dir / "core_patient", out, release_id="PRIVACY")
    problems = []
    for p in out.rglob("*"):
        if "private" in p.parts:
            problems.append(str(p))
    # the master key bytes must never appear anywhere in published output
    content = _read_released(out)
    if _MASTER_KEY.decode() in content:
        problems.append("MASTER_KEY_LEAKED")
    return CheckResult("private_artifacts_and_secrets_never_public", not problems, f"problems={problems}")


def _check_cli_subprocess_smoke(fixtures_dir: Path, work: Path, repo_root: Path) -> CheckResult:
    key_file = work / "cli_key.txt"
    key_file.write_text("cli-smoke-master-secret-key-material-32bytes!", encoding="utf-8")
    out = work / "cli_out"
    sanitize_out = subprocess.run(
        [sys.executable, "-m", "nijsetu", "sanitize", "--input", str(fixtures_dir / "core_patient"),
         "--output", str(out), "--policy", "balanced", "--release-id", "CLISMOKE", "--key-file", str(key_file)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    validate_out = subprocess.run(
        [sys.executable, "-m", "nijsetu", "validate", "--release", str(out)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    ok = sanitize_out.returncode in (0, 2) and validate_out.returncode == 0
    return CheckResult("cli_sanitize_and_validate_subprocess_smoke", ok,
                        f"sanitize_rc={sanitize_out.returncode} validate_rc={validate_out.returncode} "
                        f"sanitize_stderr={sanitize_out.stderr[-300:]} validate_stderr={validate_out.stderr[-300:]}")


def _check_identifying_header_text_removed(fixtures_dir: Path, work: Path) -> CheckResult:
    """Gap 1: an unfamiliar name following a real identifying label in a
    CSV header must be detected and renamed, not survive verbatim."""
    inp = work / "header_leak_in"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "registration.csv").write_text("UHID,Patient: Qzylom Narex\nA01,clear\n")
    out = work / "header_leak_out"
    outcome = _sanitize(inp, out, config=AppConfig(source_system_map=(("registration.csv", "HOSP"),)))
    content = _read_released(out) if outcome.status == "RELEASED" else ""
    ok = outcome.status == "RELEASED" and "Qzylom Narex" not in content and "SANITIZED_HEADER" in content
    ok = ok and validate_release(out).passed
    return CheckResult("identifying_csv_header_text_removed", ok, f"status={outcome.status} content_has_name={'Qzylom Narex' in content}")


def _check_nested_fhir_text_inspected(fixtures_dir: Path, work: Path) -> CheckResult:
    """Gap 2: a name embedded in valueCodeableConcept.text (not just the
    valueString field the original repair covered) must be masked."""
    inp = work / "fhir_nested_in"
    inp.mkdir(parents=True, exist_ok=True)
    bundle = {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:p", "resource": {"resourceType": "Patient", "id": "p",
                                                     "identifier": [{"system": "urn:local:uhid", "value": "A01"}]}},
            {"fullUrl": "urn:uuid:o", "resource": {
                "resourceType": "Observation", "id": "o", "status": "final",
                "subject": {"reference": "urn:uuid:p"},
                "code": {"text": "Blood Glucose"},
                "valueCodeableConcept": {"text": "Patient: Qzylom Narex"},
            }},
        ],
    }
    (inp / "bundle.json").write_text(json.dumps(bundle))
    out = work / "fhir_nested_out"
    outcome = _sanitize(inp, out, config=AppConfig(source_system_map=(("bundle.json", "HOSP"),)))
    content = _read_released(out) if outcome.status == "RELEASED" else ""
    ok = outcome.status == "RELEASED" and "Qzylom Narex" not in content and "Blood Glucose" in content
    return CheckResult("nested_fhir_text_inspected", ok, f"status={outcome.status} content_has_name={'Qzylom Narex' in content}")


def _check_risk_matches_actual_output(fixtures_dir: Path, work: Path) -> CheckResult:
    """Compare actual shifted-month groups and singleton counts to the report."""
    from ..phase2 import keys

    inp = work / "risk_vs_output_in"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "registration.csv").write_text(
        "UHID,Patient Name,Admission Date,Discharge Date\n"
        "A01,Ramesh Gupta,2024-10-12,2024-10-17\nA02,Suresh Kumar,2024-10-12,2024-10-17\n"
    )
    out = work / "risk_vs_output_out"
    original_shift = keys.date_shift_days
    keys.date_shift_days = lambda _key, cluster, _lo, _hi: -23 if cluster == "0:0" else -1
    try:
        outcome = _sanitize(inp, out, config=AppConfig(source_system_map=(("registration.csv", "HOSP"),)))
    finally:
        keys.date_shift_days = original_shift
    content = _read_released(out) if outcome.status == "RELEASED" else ""
    exact_dates = len(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", content))
    coverage = outcome.report.get("risk_after", {}).get("qi_coverage", {}).get("visit_period") if outcome.status == "RELEASED" else None
    rows = list(csv.DictReader(io.StringIO(content))) if content else []
    groups = dict(Counter(str((None, None, row["Admission Date"][:7])) for row in rows))
    after = outcome.report.get("risk_after", {})
    ok = (
        outcome.status == "RELEASED" and exact_dates == 4 and coverage == 1.0
        and len(groups) == 2 and after.get("group_sizes") == groups
        and after.get("singleton_count") == 2 and after.get("singleton_rate") == 1.0
    )
    detail = f"status={outcome.status} exported_groups={groups} reported_groups={after.get('group_sizes')}"
    return CheckResult("risk_report_matches_actual_output", ok, detail)


def _check_json_wrapper_preserved(fixtures_dir: Path, work: Path) -> CheckResult:
    """Gap 4: a configured --record-wrapper-key wrapper object (and its
    other metadata) must survive on output, not collapse to a bare array."""
    inp = work / "wrapper_in"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "batch.json").write_text('{"batchLabel": "synthetic-demo", "records": [{"uhid": "A01", "labReading": 8.200}]}')
    out = work / "wrapper_out"
    config = AppConfig(source_system_map=(("batch.json", "HOSP"),), record_wrapper_key="records")
    outcome = _sanitize(inp, out, config=config)
    ok = outcome.status == "RELEASED"
    data = None
    if ok:
        raw_text = (out / "released" / "file_0.json").read_text(encoding="utf-8")
        data = json.loads(raw_text)
        ok = (
            data.get("batchLabel") == "synthetic-demo"
            and isinstance(data.get("records"), list) and len(data["records"]) == 1
            and "8.200" in raw_text
        )
    return CheckResult("json_record_wrapper_preserved", ok, f"status={outcome.status} data={data}")


def _check_report_status_and_manifest_consistent(fixtures_dir: Path, work: Path) -> CheckResult:
    """Gap 5: the published report.json must carry a final status
    consistent with hold_index.json BEFORE it was hashed into manifest.json,
    and the CLI-returned status must agree with the on-disk report."""
    out = work / "status_out"
    outcome = _sanitize(fixtures_dir / "core_patient", out, release_id="STATUSCHK")
    on_disk = json.loads((out / "report.json").read_text(encoding="utf-8")) if out.exists() else {}
    validation = validate_release(out) if out.exists() else None
    status_check = next((c for c in validation.checks if c["name"] == "report_status_consistent"), None) if validation else None
    ok = (
        outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
        and on_disk.get("status") == outcome.status == outcome.report.get("status")
        and status_check is not None and status_check["passed"]
    )
    return CheckResult("report_status_finalized_before_hash", ok, f"status={outcome.status} on_disk_status={on_disk.get('status')}")


def _check_forced_staged_validation_failure_blocks_publish(fixtures_dir: Path, work: Path) -> CheckResult:
    """A forced failure of the independent staged-validation gate (run just
    before promotion) must leave no newly published release -- exercises
    the same no-partial-release guarantee as the write-failure check, but
    for the validation step instead of the filesystem rename."""
    import nijsetu.phase2.sanitize as sanitize_mod

    out = work / "staged_validation_fail_out"
    original_validate = sanitize_mod.validate_release

    def _always_fail(_release_dir):
        from nijsetu.phase2.validate import ValidationResult
        return ValidationResult(passed=False, checks=[{"name": "injected_failure", "passed": False, "detail": "forced"}])

    sanitize_mod.validate_release = _always_fail
    try:
        outcome = _sanitize(fixtures_dir / "core_patient", out, release_id="STAGEDFAIL")
    finally:
        sanitize_mod.validate_release = original_validate
    ok = outcome.status == "BLOCKED" and not out.exists()
    return CheckResult("forced_staged_validation_failure_leaves_no_release", ok, f"status={outcome.status} output_exists={out.exists()}")


def _check_concurrent_runs_isolated(fixtures_dir: Path, work: Path) -> CheckResult:
    """Two sanitize runs against different inputs, targeting different
    --output directories under the same parent, executed concurrently, must
    not interfere with each other's staging or published content."""
    import threading

    parent = work / "concurrent_parent"
    parent.mkdir(parents=True, exist_ok=True)
    inp_a, inp_b = work / "concurrent_in_a", work / "concurrent_in_b"
    for inp, uhid, name in ((inp_a, "A01", "Ramesh Gupta"), (inp_b, "B01", "Suresh Kumar")):
        inp.mkdir(parents=True, exist_ok=True)
        (inp / "registration.csv").write_text(f"UHID,Patient Name\n{uhid},{name}\n")
    out_a, out_b = parent / "out_a", parent / "out_b"
    results: dict[str, object] = {}

    def _run(key, inp, out, release_id):
        results[key] = _sanitize(inp, out, release_id=release_id, config=AppConfig(source_system_map=(("registration.csv", "HOSP"),)))

    t1 = threading.Thread(target=_run, args=("a", inp_a, out_a, "GATE_CONC_A"))
    t2 = threading.Thread(target=_run, args=("b", inp_b, out_b, "GATE_CONC_B"))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)

    ok = (
        results.get("a") is not None and results.get("b") is not None
        and results["a"].status == "RELEASED" and results["b"].status == "RELEASED"
        and not list(parent.glob(".nijsetu_stage_*"))
    )
    status_a = getattr(results.get("a"), "status", None)
    status_b = getattr(results.get("b"), "status", None)
    leftover = list(parent.glob(".nijsetu_stage_*"))
    detail = f"a={status_a} b={status_b} leftover_staging={leftover}"
    return CheckResult("concurrent_runs_isolated", ok, detail)


def run_gate_phase2(output_dir: Path) -> int:
    if output_dir.exists() and any(output_dir.iterdir()):
        print(json.dumps({"status": "ERROR", "reason_code": "OUTPUT_DIR_NOT_EMPTY"}), file=sys.stderr)
        return 4
    output_dir.mkdir(parents=True, exist_ok=True)

    fixtures_dir = _fixtures_dir()
    if fixtures_dir is None:
        (output_dir / "summary.json").write_text(json.dumps({"status": "BLOCKED", "reason_code": "FIXTURES_NOT_FOUND"}, indent=2))
        print(json.dumps({"status": "BLOCKED", "reason_code": "FIXTURES_NOT_FOUND"}), file=sys.stderr)
        return 3

    repo_root = fixtures_dir.parent.parent.parent.parent
    work = Path(tempfile.mkdtemp(prefix="nijsetu_gate2_work_"))
    try:
        checks = [
            _check_mixed_batch_valid_release(fixtures_dir, work),
            _check_direct_identifiers_removed(fixtures_dir, work),
            _check_consistent_pseudonyms(fixtures_dir, work),
            _check_release_rotation(fixtures_dir, work),
            _check_replay_determinism(fixtures_dir, work),
            _check_clinical_measurements_survive(fixtures_dir, work),
            _check_fhir_references_resolve(fixtures_dir, work),
            _check_small_cohort_retained(fixtures_dir, work),
            _check_unresolved_standalone(fixtures_dir, work),
            _check_malformed_unsupported_holds(fixtures_dir, work),
            _check_filename_leak_blocked(fixtures_dir, work),
            _check_fault_injection_no_partial_release(fixtures_dir, work),
            _check_manifest_tampering_detected(fixtures_dir, work),
            _check_private_artifacts_never_public(fixtures_dir, work),
            _check_cli_subprocess_smoke(fixtures_dir, work, repo_root),
            _check_identifying_header_text_removed(fixtures_dir, work),
            _check_nested_fhir_text_inspected(fixtures_dir, work),
            _check_risk_matches_actual_output(fixtures_dir, work),
            _check_json_wrapper_preserved(fixtures_dir, work),
            _check_report_status_and_manifest_consistent(fixtures_dir, work),
            _check_forced_staged_validation_failure_blocks_publish(fixtures_dir, work),
            _check_concurrent_runs_isolated(fixtures_dir, work),
        ]
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for check in checks:
        (output_dir / f"{check.name}.json").write_text(json.dumps({
            "name": check.name, "passed": check.passed, "detail": check.detail, "evidence": check.evidence,
        }, indent=2), encoding="utf-8")

    all_passed = all(c.passed for c in checks)
    summary = {"status": "PASS" if all_passed else "FAIL", "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks]}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all_passed else 2
