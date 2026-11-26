"""Gate 1 runner: must return success only when its real checks actually pass."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_gate_phase1_passes_on_the_real_implementation(tmp_path: Path):
    out_dir = tmp_path / "gate_out"
    result = subprocess.run(
        [sys.executable, "-m", "nijsetu", "gate", "phase1", "--output", str(out_dir)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["status"] == "PASS"
    failed = [c for c in summary["checks"] if not c["passed"]]
    assert not failed, failed


def test_gate_phase1_rejects_nonempty_output_dir(tmp_path: Path):
    out_dir = tmp_path / "gate_out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("x")
    result = subprocess.run(
        [sys.executable, "-m", "nijsetu", "gate", "phase1", "--output", str(out_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 4


def _fake_record(file_path: str, record_no: int, status: str, cluster: str | None, reason_codes=None) -> dict:
    return {
        "file_path": file_path, "record_key": f"0:{record_no}", "link_status": status,
        "cluster_id": cluster, "reason_codes": reason_codes or [],
    }


def test_corroborated_links_check_fails_when_group_is_only_partially_linked():
    """Non-vacuousness: 3/4 of a group accepted into one cluster (the 4th
    silently lost to HELD) must fail, not pass because the 3 that remain
    happen to agree."""
    from nijsetu.gate.phase1 import _check_corroborated_links

    private = {"records": [
        _fake_record("arif_corroborated/registration.csv", 0, "ACCEPTED", "c1"),
        _fake_record("arif_corroborated/consultation.json", 1, "ACCEPTED", "c1"),
        _fake_record("arif_corroborated/discharge.txt", 2, "ACCEPTED", "c1"),
        _fake_record("arif_corroborated/opd_note.txt", 3, "HELD", None),
        _fake_record("meera_corroborated/registration.csv", 4, "ACCEPTED", "c2"),
        _fake_record("meera_corroborated/consultation.json", 5, "ACCEPTED", "c2"),
        _fake_record("meera_corroborated/discharge.txt", 6, "ACCEPTED", "c2"),
    ]}
    result = _check_corroborated_links(private)
    assert result.passed is False


def test_corroborated_links_check_fails_when_fixture_is_missing():
    from nijsetu.gate.phase1 import _check_corroborated_links

    private = {"records": [
        _fake_record("meera_corroborated/registration.csv", 0, "ACCEPTED", "c2"),
        _fake_record("meera_corroborated/consultation.json", 1, "ACCEPTED", "c2"),
        _fake_record("meera_corroborated/discharge.txt", 2, "ACCEPTED", "c2"),
    ]}
    result = _check_corroborated_links(private)
    assert result.passed is False
    assert "arif" in result.detail


def test_hard_negatives_check_fails_when_a_fixture_is_missing_not_vacuously_passes():
    """An empty/missing hard-negative fixture must not read as proof of
    "no incorrect merges" -- it proves nothing."""
    from nijsetu.gate.phase1 import _check_hard_negatives

    private = {"records": []}  # every hard-negative fixture is "missing"
    result = _check_hard_negatives(private)
    assert result.passed is False
    assert "MISSING FIXTURE DATA" in result.detail


def test_hard_negatives_check_fails_on_a_real_incorrect_merge():
    from nijsetu.gate.phase1 import _check_hard_negatives

    records = [
        _fake_record("arif_corroborated/registration.csv", 0, "ACCEPTED", "shared"),
        _fake_record("arif_corroborated/consultation.json", 1, "ACCEPTED", "shared"),
        _fake_record("arif_corroborated/discharge.txt", 2, "ACCEPTED", "shared"),
        _fake_record("arif_corroborated/opd_note.txt", 3, "ACCEPTED", "shared"),
        _fake_record("arif_different/registration.csv", 4, "ACCEPTED", "shared"),  # incorrectly merged
        _fake_record("household_shared/family.csv", 5, "SINGLETON", "h0"),
        _fake_record("household_shared/family.csv", 6, "SINGLETON", "h1"),
        _fake_record("uhid_cross_issuer_hospitalA/hospitalA.csv", 7, "SINGLETON", "u0"),
        _fake_record("uhid_cross_issuer_hospitalB/hospitalB_fhir.json", 8, "SINGLETON", "u1"),
        _fake_record("conflicting_ids/same_hospital.csv", 9, "HELD", None),
        _fake_record("conflicting_ids/same_hospital.csv", 10, "HELD", None),
        _fake_record("bridge_conflict/same_hospital.csv", 11, "SINGLETON", "b0"),
        _fake_record("bridge_conflict/same_hospital.csv", 12, "SINGLETON", "b1"),
        _fake_record("bridge_conflict/same_hospital.csv", 13, "SINGLETON", "b2"),
        _fake_record("source_scope_unknown/hospitalA/records.csv", 14, "SINGLETON", "s0"),
        _fake_record("source_scope_unknown/hospitalB/records.csv", 15, "SINGLETON", "s1"),
        _fake_record("source_scope_configured_distinct/hospitalA/records.csv", 16, "SINGLETON", "d0"),
        _fake_record("source_scope_configured_distinct/hospitalB/records.csv", 17, "SINGLETON", "d1"),
    ]
    result = _check_hard_negatives({"records": records})
    assert result.passed is False
    assert "arif_different merged into arif_corroborated" in result.detail


def test_fhir_subject_reference_check_fails_when_ambiguous_case_resolves_arbitrarily():
    from nijsetu.gate.phase1 import _check_fhir_subject_reference_regression

    records = [
        _fake_record("fhir_patient_link/two_patients_bundle.json", 0, "ACCEPTED", "pA"),
        _fake_record("fhir_patient_link/two_patients_bundle.json", 1, "ACCEPTED", "pA"),
        _fake_record("fhir_patient_link/two_patients_bundle.json", 2, "ACCEPTED", "pB"),
        _fake_record("fhir_patient_link/two_patients_bundle.json", 3, "ACCEPTED", "pB"),
        _fake_record("fhir_patient_link/performer_not_patient.json", 0, "SINGLETON", "doc"),
        _fake_record("fhir_patient_link/performer_not_patient.json", 1, "SINGLETON", "obs"),
        _fake_record("fhir_patient_link/ambiguous_subject_bundle.json", 0, "SINGLETON", "pX"),
        _fake_record("fhir_patient_link/ambiguous_subject_bundle.json", 1, "SINGLETON", "pY"),
        # BUG: arbitrarily resolved into pX's cluster instead of being held.
        _fake_record("fhir_patient_link/ambiguous_subject_bundle.json", 2, "ACCEPTED", "pX"),
        _fake_record("fhir_patient_link/duplicate_logical_id_bundle.json", 0, "SINGLETON", "alpha"),
        _fake_record("fhir_patient_link/duplicate_logical_id_bundle.json", 1, "SINGLETON", "beta"),
        _fake_record("fhir_patient_link/duplicate_logical_id_bundle.json", 2, "SINGLETON", "enc"),
    ]
    result = _check_fhir_subject_reference_regression({"records": records})
    assert result.passed is False


def test_gate_fails_loudly_when_a_check_is_broken(tmp_path: Path, monkeypatch):
    """Fault injection: a real implementation regression must flip gate to FAIL,
    not silently keep passing. We intentionally break clinical detection by
    aiming the gate's clinical-semantics check at a module path that cannot
    satisfy it, via monkeypatching the check function to force a failure.
    """
    from nijsetu.gate import phase1 as gate_mod

    original = gate_mod._check_clinical_semantics
    try:
        gate_mod._check_clinical_semantics = lambda: gate_mod.CheckResult(
            "clinical_semantics", False, "forced failure for fault-injection test",
        )
        out_dir = tmp_path / "gate_out"
        rc = gate_mod.run_gate_phase1(out_dir)
        assert rc == 2
        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["status"] == "FAIL"
    finally:
        gate_mod._check_clinical_semantics = original
