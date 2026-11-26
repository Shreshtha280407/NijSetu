"""Gate 2 runner: must return success only when its real checks actually pass."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_gate_phase2_passes_on_the_real_implementation(tmp_path: Path):
    out_dir = tmp_path / "gate2_out"
    result = subprocess.run(
        [sys.executable, "-m", "nijsetu", "gate", "phase2", "--output", str(out_dir)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["status"] == "PASS"
    failed = [c for c in summary["checks"] if not c["passed"]]
    assert not failed, failed


def test_gate_phase2_rejects_nonempty_output_dir(tmp_path: Path):
    out_dir = tmp_path / "gate2_out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("x")
    result = subprocess.run(
        [sys.executable, "-m", "nijsetu", "gate", "phase2", "--output", str(out_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 4


def test_gate_phase2_fails_loudly_when_a_check_is_broken(tmp_path: Path, monkeypatch):
    """Fault injection: a deliberately-broken check function must cause the
    whole gate to FAIL, proving the gate isn't vacuously green."""
    import nijsetu.gate.phase2 as gate2

    original = gate2._check_direct_identifiers_removed

    def _broken(fixtures_dir, work):
        result = original(fixtures_dir, work)
        result.passed = False
        result.detail = "INJECTED_FAILURE"
        return result

    monkeypatch.setattr(gate2, "_check_direct_identifiers_removed", _broken)
    out_dir = tmp_path / "gate2_broken"
    rc = gate2.run_gate_phase2(out_dir)
    assert rc == 2
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["status"] == "FAIL"
    broken = next(c for c in summary["checks"] if c["name"] == "direct_identifiers_removed")
    assert broken["passed"] is False
