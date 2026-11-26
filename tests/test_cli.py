"""CLI contract: real subprocess invocations, exit codes, --help, invocation errors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "nijsetu", *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_help_exits_zero():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "analyze" in result.stdout and "gate" in result.stdout


def test_analyze_help_exits_zero():
    result = run_cli("analyze", "--help")
    assert result.returncode == 0
    assert "--input" in result.stdout and "--output" in result.stdout


def test_missing_subcommand_is_invocation_error():
    result = run_cli()
    assert result.returncode == 4


def test_unknown_gate_name_is_invocation_error(tmp_path: Path):
    result = run_cli("gate", "bogus", "--output", str(tmp_path / "out"))
    assert result.returncode == 4


def test_missing_required_flag_is_invocation_error():
    result = run_cli("analyze", "--input", "/tmp")
    assert result.returncode == 4


def test_nonexistent_input_path_is_invocation_error(tmp_path: Path):
    result = run_cli("analyze", "--input", str(tmp_path / "does_not_exist"), "--output", str(tmp_path / "out"))
    assert result.returncode == 4


def test_analyze_clean_run_exits_zero(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "r.csv").write_text("Patient Name,UHID\nRavi Teja,UH1\n")
    out_dir = tmp_path / "out"
    result = run_cli("analyze", "--input", str(in_dir), "--output", str(out_dir))
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["status"] == "CLEAN"
    assert (out_dir / "report.json").exists()
    assert (out_dir / "private" / "analysis.json").exists()


def test_analyze_with_holds_exits_two(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "bad.csv").write_text("Patient Name,Patient Name\nA,B\n")
    out_dir = tmp_path / "out"
    result = run_cli("analyze", "--input", str(in_dir), "--output", str(out_dir))
    assert result.returncode == 2


def test_analyze_output_dir_must_be_fresh(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "r.csv").write_text("Patient Name,UHID\nA,1\n")
    out_dir = tmp_path / "out"
    first = run_cli("analyze", "--input", str(in_dir), "--output", str(out_dir))
    assert first.returncode == 0
    second = run_cli("analyze", "--input", str(in_dir), "--output", str(out_dir))
    assert second.returncode == 4


def test_private_artifact_has_no_world_or_group_permissions(tmp_path: Path):
    import os
    import stat

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "r.csv").write_text("Patient Name,UHID\nA,1\n")
    out_dir = tmp_path / "out"
    run_cli("analyze", "--input", str(in_dir), "--output", str(out_dir))
    mode = stat.S_IMODE(os.stat(out_dir / "private" / "analysis.json").st_mode)
    assert mode == (stat.S_IRUSR | stat.S_IWUSR)
