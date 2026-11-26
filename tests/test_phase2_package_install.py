"""Built-wheel sanitize/validate smoke test, run entirely outside the source
checkout -- offline install (--no-index) into a fresh venv, then a real
`sanitize` + `validate` invocation against the installed package only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_installed_wheel_sanitize_and_validate_work_offline(tmp_path: Path):
    vendor_wheels = REPO_ROOT / "vendor" / "wheels"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(tmp_path.glob("nijsetu-*.whl"))
    assert wheels

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = venv_dir / "bin" / "python"
    install = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-index",
         "--find-links", str(tmp_path), "--find-links", str(vendor_wheels), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr

    outside_dir = tmp_path / "outside_repo_workdir"
    outside_dir.mkdir()
    (outside_dir / "input").mkdir()
    (outside_dir / "input" / "tiny.csv").write_text("Patient Name,UHID\nMeera Sharma,UH1\n", encoding="utf-8")
    key_file = outside_dir / "key.txt"
    key_file.write_text("offline-wheel-smoke-test-master-key-material!!!")

    sanitize_run = subprocess.run(
        [str(venv_python), "-m", "nijsetu", "sanitize", "--input", str(outside_dir / "input"),
         "--output", str(outside_dir / "release"), "--policy", "balanced",
         "--release-id", "WHEELSMOKE", "--key-file", str(key_file)],
        capture_output=True, text=True, cwd=str(outside_dir.parent),
    )
    assert sanitize_run.returncode in (0, 2), sanitize_run.stderr
    report = json.loads(sanitize_run.stdout)
    assert report["status"] in ("RELEASED", "RELEASED_WITH_HOLDS")

    validate_run = subprocess.run(
        [str(venv_python), "-m", "nijsetu", "validate", "--release", str(outside_dir / "release")],
        capture_output=True, text=True, cwd=str(outside_dir.parent),
    )
    assert validate_run.returncode == 0, validate_run.stderr
    assert json.loads(validate_run.stdout)["passed"] is True

    installed_pkg = next(venv_dir.rglob("site-packages/nijsetu"))
    leaked = subprocess.run(
        ["grep", "-rlE", r"requests|urllib3?\.request|http\.client|socket\.connect|aiohttp", str(installed_pkg)],
        capture_output=True, text=True,
    )
    assert leaked.returncode == 1, f"network-capable import found in installed package: {leaked.stdout}"
