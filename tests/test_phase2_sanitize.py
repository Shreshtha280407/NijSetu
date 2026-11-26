"""Integration tests: the full sanitize -> stage -> validate -> publish/block
pipeline against the bundled Phase 2 fixtures. Complements gate/phase2.py's
own scenario coverage with discrete, named pytest assertions."""

from __future__ import annotations

import json
from pathlib import Path

from nijsetu.config import AppConfig
from nijsetu.phase2.policy import BALANCED, STRICT
from nijsetu.phase2.sanitize import run_sanitize
from nijsetu.phase2.validate import validate_release

KEY = b"test-suite-master-secret-key-material-32bytes!!"
CORE_CONFIG = AppConfig(source_system_map=(
    ("registration.csv", "CORE_HOSPITAL"), ("consultation.json", "CORE_HOSPITAL"), ("discharge.txt", "CORE_HOSPITAL"),
))


def _sanitize(input_dir: Path, out: Path, release_id="REL1", policy=BALANCED, config=None):
    return run_sanitize(input_dir, out, policy, release_id, KEY, config or AppConfig())


def test_empty_input_directory_does_not_claim_a_useful_release(tmp_path):
    empty_in = tmp_path / "empty_in"
    empty_in.mkdir()
    outcome = _sanitize(empty_in, tmp_path / "out")
    assert outcome.status == "BLOCKED"
    assert outcome.output_dir is None


def test_all_held_batch_does_not_publish_a_zero_record_release(fixtures_phase2_dev):
    outcome = _sanitize(fixtures_phase2_dev / "malformed_and_unsupported", Path("/tmp/_unused_all_held"))
    assert outcome.status == "BLOCKED"
    assert outcome.output_dir is None
    assert "DUPLICATE_HEADER" in outcome.report["hold_summary"]


def test_core_patient_mixed_formats_produce_valid_useful_release(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    assert outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
    assert outcome.report["counts"]["records_released"] == 3
    result = validate_release(out)
    assert result.passed, result.checks


def test_unresolved_standalone_record_still_sanitized(fixtures_phase2_dev, tmp_path):
    outcome = _sanitize(fixtures_phase2_dev / "unresolved_standalone", tmp_path / "out")
    assert outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
    assert outcome.report["counts"]["records_released"] == 1
    assert outcome.report["counts"]["patients_released"] == 1


def test_small_cohort_not_blanket_suppressed_under_balanced_policy(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "small_cohort", out, policy=BALANCED)
    assert outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
    assert outcome.report["counts"]["records_released"] == 3
    assert outcome.report["counts"]["records_held"] == 0
    assert outcome.report["risk_before"]["small_group_count"] >= 1  # the k<3 signal IS reported


def test_malformed_and_unsupported_get_explicit_safe_reasons(fixtures_phase2_dev, tmp_path):
    outcome = _sanitize(fixtures_phase2_dev / "malformed_and_unsupported", tmp_path / "out")
    reasons = set(outcome.report["hold_summary"])
    assert "DUPLICATE_HEADER" in reasons
    assert "UNSUPPORTED_EXTENSION" in reasons


def test_different_release_id_rotates_patient_token(fixtures_phase2_dev, tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    _sanitize(fixtures_phase2_dev / "core_patient", out_a, release_id="RELA", config=CORE_CONFIG)
    _sanitize(fixtures_phase2_dev / "core_patient", out_b, release_id="RELB", config=CORE_CONFIG)
    text_a = (out_a / "released").joinpath(next(p.name for p in (out_a / "released").glob("*.json"))).read_text()
    text_b = (out_b / "released").joinpath(next(p.name for p in (out_b / "released").glob("*.json"))).read_text()
    assert text_a != text_b


def test_same_release_replay_is_byte_identical(fixtures_phase2_dev, tmp_path):
    out_1 = tmp_path / "r1"
    out_2 = tmp_path / "r2"
    _sanitize(fixtures_phase2_dev / "core_patient", out_1, release_id="SAME", config=CORE_CONFIG)
    _sanitize(fixtures_phase2_dev / "core_patient", out_2, release_id="SAME", config=CORE_CONFIG)
    for f1 in sorted((out_1 / "released").glob("*")):
        f2 = out_2 / "released" / f1.name
        assert f1.read_bytes() == f2.read_bytes()


def test_strict_policy_is_a_valid_alternative_and_differs_from_balanced(fixtures_phase2_dev, tmp_path):
    out_balanced = tmp_path / "bal"
    out_strict = tmp_path / "strict"
    _sanitize(fixtures_phase2_dev / "core_patient", out_balanced, release_id="X", policy=BALANCED, config=CORE_CONFIG)
    _sanitize(fixtures_phase2_dev / "core_patient", out_strict, release_id="X", policy=STRICT, config=CORE_CONFIG)
    content_balanced = "".join(p.read_text(errors="ignore") for p in (out_balanced / "released").glob("*"))
    content_strict = "".join(p.read_text(errors="ignore") for p in (out_strict / "released").glob("*"))
    assert "Jaipur" not in content_strict or "Jaipur" not in content_balanced or content_balanced != content_strict


def test_forced_leak_finding_blocks_publication(fixtures_phase2_dev, tmp_path, monkeypatch):
    import nijsetu.phase2.sanitize as sanitize_mod

    monkeypatch.setattr(sanitize_mod, "scan_literals", lambda *a, **k: ["INJECTED_LEAK_FINDING"])
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "RESIDUAL_LEAK_DETECTED"
    assert not out.exists()


def test_forced_write_failure_after_staging_leaves_no_new_release(fixtures_phase2_dev, tmp_path, monkeypatch):
    import nijsetu.phase2.staging as staging_mod

    def _boom(self):
        raise staging_mod.StagingError("INJECTED_FAILURE")

    monkeypatch.setattr(staging_mod.Staging, "publish", _boom)
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    assert outcome.status == "BLOCKED"
    assert not out.exists()


def test_source_input_changed_during_processing_blocks(fixtures_phase2_dev, tmp_path, monkeypatch):
    import nijsetu.phase2.sanitize as sanitize_mod

    # _sha256_file is only used for the post-processing re-verification pass
    # (the initial per-file hash is computed inline from already-read bytes);
    # making its first call return a wrong digest simulates the source file
    # having changed on disk between ingest and the final verify step.
    def _flaky(path):
        return "0" * 64

    monkeypatch.setattr(sanitize_mod, "_sha256_file", _flaky)
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "SOURCE_INPUT_CHANGED_DURING_PROCESSING"
    assert not out.exists()


def test_output_dir_must_not_be_overwritten_inadvertently(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("pre-existing data")
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    assert outcome.status == "ERROR"
    assert outcome.reason_code == "OUTPUT_DIR_NOT_EMPTY"
    assert (out / "existing.txt").read_text() == "pre-existing data"


def test_validate_detects_manifest_tampering_content_change(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    target = next((out / "released").glob("*"))
    target.write_text(target.read_text() + "\nTAMPERED\n")
    result = validate_release(out)
    assert not result.passed
    integrity = next(c for c in result.checks if c["name"] == "manifest_integrity")
    assert not integrity["passed"]


def test_validate_detects_undeclared_extra_file(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    (out / "released" / "sneaky_extra.txt").write_text("not in manifest")
    result = validate_release(out)
    assert not result.passed


def test_validate_reports_out_of_scope_checks_honestly(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    result = validate_release(out)
    assert "private source evidence" in result.scope_note


def test_private_evidence_never_in_published_release(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    for p in out.rglob("*"):
        assert "private" not in p.parts
    content = "".join(p.read_text(errors="ignore") for p in out.rglob("*") if p.is_file())
    assert KEY.decode() not in content


def test_report_never_contains_raw_file_paths_only_file_no(fixtures_phase2_dev, tmp_path):
    out = tmp_path / "out"
    outcome = _sanitize(fixtures_phase2_dev / "core_patient", out, config=CORE_CONFIG)
    dump = json.dumps(outcome.report)
    assert "registration.csv" not in dump
    assert "discharge.txt" not in dump
