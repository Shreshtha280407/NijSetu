"""Gate 0 regression suite: the Secret Wards organizer adapter.

Covers the behaviors specific to organizer integration that the existing
Phase 1/2 suites do not exercise: RAKSHA_SECRET-keyed master-key
derivation, the official output contract (sanitized/, report.json,
records.jsonl), determinism/rotation through the adapter, pseudonym-in-
record consistency, filename-safety renaming, and the per-file leak
quarantine that replaces a whole-release block when organizer_contract is
enabled. `tests/test_phase2_sanitize.py` / `tests/test_phase2_*` continue
to cover the default (organizer_contract=False) internal contract
unchanged -- nothing here touches those fixtures or assertions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from nijsetu.config import AppConfig
from nijsetu.organizer import OrganizerError, build_output
from nijsetu.phase2 import keys
from nijsetu.phase2.policy import BALANCED
from nijsetu.phase2.sanitize import run_sanitize

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_mixed_fixture(root: Path) -> None:
    (root / "registration").mkdir(parents=True)
    (root / "notes").mkdir(parents=True)
    (root / "fhir").mkdir(parents=True)
    (root / "registration" / "Meera_Sharma_UH100.csv").write_text(
        "Patient Name,UHID,Phone,Age,DOB\n"
        "Meera Sharma,UH100,9876543210,45,1979-01-15\n"
        "Arif Khan,UH101,9123456780,30,1994-05-20\n",
        encoding="utf-8",
    )
    (root / "notes" / "clean_note.txt").write_text(
        "Patient: Meera Sharma. Dr. Ramesh advised Dolo 650 1-0-1 x 5 days.",
        encoding="utf-8",
    )
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": {
            "resourceType": "Patient", "id": "p1", "name": [{"text": "Meera Sharma"}],
            "gender": "female", "birthDate": "1979-01-15",
            "identifier": [{"system": "urn:uhid", "value": "UH100"}],
        }}],
    }
    (root / "fhir" / "bundle1.json").write_text(json.dumps(bundle), encoding="utf-8")


# --------------------------------------------------------------------- keys


def test_derive_master_key_from_secret_is_deterministic_and_secret_sensitive():
    a1 = keys.derive_master_key_from_secret("practice")
    a2 = keys.derive_master_key_from_secret("practice")
    b = keys.derive_master_key_from_secret("different-secret")
    assert a1 == a2
    assert a1 != b
    assert len(a1) == 32  # full HMAC-SHA256 digest, independent of input length


def test_derive_master_key_rejects_empty_secret():
    try:
        keys.derive_master_key_from_secret("")
        assert False, "expected KeyError_ for an empty secret"
    except keys.KeyError_ as exc:
        assert exc.reason_code == "RAKSHA_SECRET_EMPTY"


# --------------------------------------------------------------- build_output


def test_build_output_produces_the_official_contract_shape(tmp_path):
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out = tmp_path / "out"
    code = build_output(inp, out, "practice")
    assert code == 0
    assert (out / "sanitized").is_dir()
    assert (out / "report.json").is_file()
    assert (out / "records.jsonl").is_file()

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "raksha-report/1"
    for key in ("files_total", "files_sanitized", "files_quarantined", "records_processed",
                "identifiers_detected", "records_linked", "records_generalized",
                "records_suppressed", "records_quarantined"):
        assert isinstance(report["counts"][key], (int, float))
    assert isinstance(report["timing"]["seconds"], (int, float))
    assert isinstance(report["resources"]["peak_rss_mb"], (int, float))
    assert report["counts"]["files_total"] == 3
    assert report["counts"]["files_sanitized"] == 3

    for entry in report["files"]:
        assert len(entry["input_sha256"]) == 64
        assert entry["status"] in ("sanitized", "quarantined", "suppressed")
        if entry["status"] == "sanitized":
            assert (out / entry["output"]).is_file()
        else:
            assert entry["output"] is None

    lines = [json.loads(line) for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines
    for r in lines:
        assert set(r) >= {"file", "loc", "patient"}
        assert (out / r["file"]).is_file()


def test_pseudonym_actually_appears_in_the_record_it_is_claimed_for(tmp_path):
    """SPEC.md section 6: "the patient value must actually appear inside
    that exported record" -- not merely structurally plausible."""
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out = tmp_path / "out"
    assert build_output(inp, out, "practice") == 0

    any_claimed = False
    for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["patient"] is None:
            continue
        any_claimed = True
        text = (out / r["file"]).read_text(encoding="utf-8")
        assert r["patient"] in text, f"claimed pseudonym not found in {r['file']}"
    assert any_claimed, "fixture produced no claimed pseudonym at all -- test is not exercising anything"


def test_same_secret_twice_is_byte_identical_different_secret_is_not(tmp_path):
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_c = tmp_path / "c"
    assert build_output(inp, out_a, "practice") == 0
    assert build_output(inp, out_b, "practice") == 0
    assert build_output(inp, out_c, "different-secret") == 0

    def _tree(out):
        parts = []
        for p in sorted((out / "sanitized").rglob("*")):
            if p.is_file():
                parts.append((str(p.relative_to(out)), p.read_bytes()))
        parts.append(("records.jsonl", (out / "records.jsonl").read_bytes()))
        return parts

    assert _tree(out_a) == _tree(out_b)
    assert _tree(out_a) != _tree(out_c)


def test_different_output_directory_path_does_not_change_release_content(tmp_path):
    """release_id must be fixed, never derived from --output -- a different
    output directory path/name must not rotate tokens."""
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out_1 = tmp_path / "wherever_1"
    out_2 = tmp_path / "a_totally_different_name_for_the_same_run"
    assert build_output(inp, out_1, "practice") == 0
    assert build_output(inp, out_2, "practice") == 0
    assert (out_1 / "records.jsonl").read_bytes() == (out_2 / "records.jsonl").read_bytes()


def test_missing_secret_fails_safely_without_publishing_anything(tmp_path):
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out = tmp_path / "out"
    try:
        build_output(inp, out, None)
        assert False, "expected OrganizerError for a missing secret"
    except OrganizerError as exc:
        assert exc.reason_code == "RAKSHA_SECRET_MISSING_OR_EMPTY"
    assert not (out / "report.json").exists()
    assert not (out / "sanitized").exists()


def test_secret_is_never_written_to_any_output_artifact(tmp_path):
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out = tmp_path / "out"
    secret = "super-secret-value-never-persisted-anywhere-zz9"
    assert build_output(inp, out, secret) == 0
    for p in out.rglob("*"):
        if p.is_file():
            assert secret.encode() not in p.read_bytes()


def test_unsafe_filename_is_renamed_and_declared_safe_filename_is_preserved(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "Discharge_Summary_Meera_Sharma.txt").write_text(
        "Patient: Meera Sharma. Dr. Ramesh advised rest.", encoding="utf-8",
    )
    (inp / "drug_formulary.txt").write_text("Paracetamol 500mg, Amoxicillin 250mg.", encoding="utf-8")
    out = tmp_path / "out"
    assert build_output(inp, out, "practice") == 0

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    outputs = {e["output"] for e in report["files"] if e["status"] == "sanitized"}
    assert "sanitized/drug_formulary.txt" in outputs
    assert not any("Meera" in (o or "") or "Sharma" in (o or "") for o in outputs)
    assert any(o and o.startswith("sanitized/_renamed/") for o in outputs)


def test_output_dir_must_be_empty(tmp_path):
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    out = tmp_path / "out"
    out.mkdir()
    (out / "leftover.txt").write_text("x", encoding="utf-8")
    try:
        build_output(inp, out, "practice")
        assert False, "expected OrganizerError for a non-empty output dir"
    except OrganizerError as exc:
        assert exc.reason_code == "OUTPUT_DIR_NOT_EMPTY"


# ------------------------------------------------------- per-file quarantine


def test_organizer_contract_quarantines_one_leaky_file_without_blocking_the_rest(fixtures_phase2_dev, tmp_path, monkeypatch):
    """Without organizer_contract, a single residual-leak finding blocks the
    WHOLE release (test_phase2_sanitize.py's test_forced_leak_finding_blocks_
    publication, unchanged). With it, exactly the implicated file is
    quarantined and the rest of a genuinely clean release still publishes --
    the core Gate-0 fail-safe-at-the-smallest-unit behavior."""
    import nijsetu.phase2.sanitize as sanitize_mod

    calls = {"n": 0}
    real_scan_literals = sanitize_mod.scan_literals

    def _fake_scan_literals(staging_root, file_literals, minted_tokens=frozenset()):
        calls["n"] += 1
        if calls["n"] == 1:
            return ["CONTENT_LEAK:released/file_0.json"]
        return real_scan_literals(staging_root, file_literals, minted_tokens)

    monkeypatch.setattr(sanitize_mod, "scan_literals", _fake_scan_literals)

    config = AppConfig(source_system_map=(
        ("registration.csv", "CORE_HOSPITAL"), ("consultation.json", "CORE_HOSPITAL"), ("discharge.txt", "CORE_HOSPITAL"),
    ))
    out = tmp_path / "out"
    outcome = run_sanitize(
        fixtures_phase2_dev / "core_patient", out, BALANCED, "GATE0TEST",
        b"test-suite-master-secret-key-material-32bytes!!", config, organizer_contract=True,
    )
    assert outcome.status == "RELEASED_WITH_HOLDS"
    assert outcome.output_dir is not None
    assert not (out / "released" / "file_0.json").exists()
    assert any(
        h.get("file_no") == 0 and "RESIDUAL_LEAK_DETECTED" in h.get("reason_codes", [])
        for h in json.loads((out / "hold_index.json").read_text(encoding="utf-8"))
    )
    assert len(list((out / "released").glob("*"))) == 2
    assert outcome.records_index is not None
    assert all(r["file_no"] != 0 for r in outcome.records_index)


def test_organizer_contract_still_blocks_when_leak_cannot_be_attributed_to_one_file(fixtures_phase2_dev, tmp_path, monkeypatch):
    import nijsetu.phase2.sanitize as sanitize_mod

    monkeypatch.setattr(sanitize_mod, "scan_literals", lambda *a, **k: ["UNATTRIBUTABLE_FINDING"])
    config = AppConfig(source_system_map=(
        ("registration.csv", "CORE_HOSPITAL"), ("consultation.json", "CORE_HOSPITAL"), ("discharge.txt", "CORE_HOSPITAL"),
    ))
    out = tmp_path / "out"
    outcome = run_sanitize(
        fixtures_phase2_dev / "core_patient", out, BALANCED, "GATE0TEST2",
        b"test-suite-master-secret-key-material-32bytes!!", config, organizer_contract=True,
    )
    assert outcome.status == "BLOCKED"
    assert not out.exists()


# ------------------------------------------------------- end-to-end (main.py)


def test_main_py_end_to_end_subprocess(tmp_path):
    """Mirrors what check_submission.py --run actually does: invoke the
    real raksha.toml `run` entrypoint in a fresh subprocess, twice with the
    same secret and once with a different one."""
    inp = tmp_path / "input"
    _write_mixed_fixture(inp)
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.is_file() else sys.executable

    def _run(out_name, secret):
        out = tmp_path / out_name
        env = dict(os.environ, RAKSHA_SECRET=secret)
        proc = subprocess.run(
            [python, str(REPO_ROOT / "main.py"), "--input", str(inp), "--output", str(out)],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=120,
        )
        return out, proc

    out_a, proc_a = _run("out_a", "practice")
    assert proc_a.returncode == 0, proc_a.stderr
    assert "practice" not in proc_a.stdout and "practice" not in proc_a.stderr

    out_b, proc_b = _run("out_b", "practice")
    assert proc_b.returncode == 0, proc_b.stderr
    assert (out_a / "records.jsonl").read_bytes() == (out_b / "records.jsonl").read_bytes()

    out_c, proc_c = _run("out_c", "a-rotated-secret")
    assert proc_c.returncode == 0, proc_c.stderr
    assert (out_a / "records.jsonl").read_bytes() != (out_c / "records.jsonl").read_bytes()
