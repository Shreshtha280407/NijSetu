"""CLI exit-code tests via real subprocess: `sanitize`, `validate`, `gate phase2`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(args: list[str], cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, "-m", "nijsetu", *args], capture_output=True, text=True, timeout=120, cwd=str(cwd) if cwd else None,
    )


def test_sanitize_missing_input_path_is_invocation_error(tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("x" * 32)
    result = _run(["sanitize", "--input", str(tmp_path / "nope"), "--output", str(tmp_path / "out"),
                   "--policy", "balanced", "--release-id", "R1", "--key-file", str(key)])
    assert result.returncode == 4


def test_sanitize_missing_key_file_is_invocation_error(fixtures_phase2_dev, tmp_path):
    result = _run(["sanitize", "--input", str(fixtures_phase2_dev / "core_patient"), "--output", str(tmp_path / "out"),
                   "--policy", "balanced", "--release-id", "R1", "--key-file", str(tmp_path / "nokey.txt")])
    assert result.returncode == 4
    err = json.loads(result.stderr)
    assert err["reason_code"] == "KEY_FILE_NOT_FOUND"


def test_sanitize_short_key_is_invocation_error(fixtures_phase2_dev, tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("short")
    result = _run(["sanitize", "--input", str(fixtures_phase2_dev / "core_patient"), "--output", str(tmp_path / "out"),
                   "--policy", "balanced", "--release-id", "R1", "--key-file", str(key)])
    assert result.returncode == 4
    err = json.loads(result.stderr)
    assert err["reason_code"] == "KEY_MATERIAL_TOO_SHORT"


def test_sanitize_unknown_policy_is_invocation_error(fixtures_phase2_dev, tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("x" * 32)
    result = _run(["sanitize", "--input", str(fixtures_phase2_dev / "core_patient"), "--output", str(tmp_path / "out"),
                   "--policy", "weird", "--release-id", "R1", "--key-file", str(key)])
    assert result.returncode == 4  # argparse rejects --policy choices before reaching PolicyError


def test_sanitize_never_prints_the_master_key(fixtures_phase2_dev, tmp_path):
    key_text = "super-secret-material-that-must-never-print!!!"
    key = tmp_path / "key.txt"
    key.write_text(key_text)
    result = _run(["sanitize", "--input", str(fixtures_phase2_dev / "core_patient"), "--output", str(tmp_path / "out"),
                   "--policy", "balanced", "--release-id", "R1", "--key-file", str(key)])
    assert key_text not in result.stdout
    assert key_text not in result.stderr


def test_sanitize_cli_end_to_end_and_validate(fixtures_phase2_dev, tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("x" * 32)
    out = tmp_path / "out"
    result = _run([
        "sanitize", "--input", str(fixtures_phase2_dev / "core_patient"), "--output", str(out),
        "--policy", "balanced", "--release-id", "R1", "--key-file", str(key),
        "--source-system", "registration.csv=HOSP", "--source-system", "consultation.json=HOSP",
        "--source-system", "discharge.txt=HOSP",
    ])
    assert result.returncode in (0, 2), result.stderr
    report = json.loads(result.stdout)
    assert report["status"] in ("RELEASED", "RELEASED_WITH_HOLDS")

    validate_result = _run(["validate", "--release", str(out)])
    assert validate_result.returncode == 0, validate_result.stderr
    validated = json.loads(validate_result.stdout)
    assert validated["passed"] is True


def test_validate_missing_release_path_is_invocation_error(tmp_path):
    result = _run(["validate", "--release", str(tmp_path / "nope")])
    assert result.returncode == 4


def test_gate_phase2_cli_passes():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "gate2"
        result = _run(["gate", "phase2", "--output", str(out_dir)])
        assert result.returncode == 0, result.stdout + result.stderr
        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["status"] == "PASS"
