"""Package installs cleanly and the installed CLI can reach its bundled gazetteer.

This duplicates (narrowly) what the gate's own wheel check proves, but as an
independent pytest assertion per the "package installation and bundled
resource availability" requirement -- not relying solely on the gate.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_wheel_builds_and_installs_offline_with_working_gazetteer(tmp_path: Path):
    vendor_wheels = REPO_ROOT / "vendor" / "wheels"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(tmp_path.glob("nijsetu-*.whl"))
    assert wheels, "no wheel produced"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert any(n.endswith("gazetteer/data/first_names.json") for n in names)
    assert any(n.endswith("gazetteer/data/surnames.json") for n in names)
    assert any(n.endswith("gazetteer/data/FAKER_LICENSE.txt") for n in names), (
        "the Faker-derived gazetteer's license notice must ship inside the wheel, not just the source tree"
    )

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = venv_dir / "bin" / "python"

    install = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-index",
         "--find-links", str(tmp_path), "--find-links", str(vendor_wheels), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr

    probe = subprocess.run(
        [str(venv_python), "-c",
         "from nijsetu.gazetteer import is_known_first_name, is_known_surname; "
         "assert is_known_first_name('Meera'); assert is_known_surname('Gupta'); print('OK')"],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0 and "OK" in probe.stdout, probe.stderr
