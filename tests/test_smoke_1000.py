"""1,000-record Phase 1 analysis smoke run: real processing, bounded work, determinism."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_smoke_batch import generate  # noqa: E402


def test_smoke_batch_processes_all_records_within_bounds(tmp_path: Path):
    in_dir = tmp_path / "in"
    generate(in_dir, n_patients=300)
    total_files = sum(1 for _ in in_dir.rglob("*") if _.is_file())
    assert total_files > 300  # registration.csv + consultations.json + many notes

    out_dir = tmp_path / "out"
    started = time.monotonic()
    # This batch is one hospital's mixed registration/consultation/OPD-note
    # export (see gen_smoke_batch.py) -- a real deployment declares that
    # explicitly rather than relying on an implicit same-batch guess, so the
    # smoke run does the same via --source-system (see Finding 1 / README
    # on identity.build_candidate_features's fallback-issuer scoping).
    result = subprocess.run(
        [
            sys.executable, "-m", "nijsetu", "analyze", "--input", str(in_dir), "--output", str(out_dir),
            "--source-system", "registration.csv=CITY_GENERAL_HOSPITAL",
            "--source-system", "consultations.json=CITY_GENERAL_HOSPITAL",
            "--source-system", "notes=CITY_GENERAL_HOSPITAL",
        ],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.monotonic() - started
    assert result.returncode in (0, 2), result.stderr
    assert elapsed < 60, f"smoke batch took {elapsed:.1f}s, expected well under a minute"

    report = json.loads(result.stdout)
    assert report["counts"]["documents_processed"] >= 1000
    assert report["counts"]["files_by_disposition"].get("PROCESSED", 0) == report["counts"]["files_discovered"]
    assert report["counts"]["records_held"] == 0
    assert report["linking"]["truncated_candidate_buckets"] == 0


def test_smoke_batch_is_repeatable(tmp_path: Path):
    in_dir = tmp_path / "in"
    generate(in_dir, n_patients=50)

    reports = []
    for i in range(2):
        out_dir = tmp_path / f"out{i}"
        result = subprocess.run(
            [
                sys.executable, "-m", "nijsetu", "analyze", "--input", str(in_dir), "--output", str(out_dir),
                "--source-system", "registration.csv=CITY_GENERAL_HOSPITAL",
                "--source-system", "consultations.json=CITY_GENERAL_HOSPITAL",
                "--source-system", "notes=CITY_GENERAL_HOSPITAL",
            ],
            capture_output=True, text=True, timeout=60,
        )
        report = json.loads(result.stdout)
        report.pop("timing", None)
        reports.append(report)
    assert reports[0] == reports[1]
