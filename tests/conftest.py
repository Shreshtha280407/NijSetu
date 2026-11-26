import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


@pytest.fixture(scope="session")
def fixtures_dev() -> Path:
    return REPO_ROOT / "tests" / "fixtures" / "dev"


@pytest.fixture(scope="session")
def fixtures_heldout() -> Path:
    return REPO_ROOT / "tests" / "fixtures" / "heldout"


@pytest.fixture(scope="session")
def fixtures_phase2_dev() -> Path:
    return REPO_ROOT / "tests" / "fixtures" / "phase2" / "dev"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT
