"""Generalization check: the detection rules must work on data they never saw.

Every rule in `nijsetu.detect` was developed while measuring against the
organizer's practice corpus. A rule tuned on one corpus can score perfectly on
it and recognise nothing else, and the organizer's hidden datasets are stated
to differ in exactly the ways that would expose that -- different hospitals,
different column spellings, different identifier prefixes.

This test regenerates an independent corpus (scripts/make_heldout_corpus.py),
runs the real submission entrypoint over it, and scores it the way the
organizer's own checker does. It also asserts the corpus shares no surface
forms with the practice data, because a held-out set that overlaps is not held
out.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MAKE = REPO / "scripts" / "make_heldout_corpus.py"
SCORE = REPO / "scripts" / "score_heldout.py"
PRACTICE = Path("/home/Shreshtha/documents/Secret-Wards-ToolKit/dev/input")


@pytest.fixture(scope="module")
def heldout(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("heldout")
    corpus, out = root / "corpus", root / "out"
    subprocess.run([sys.executable, str(MAKE), str(corpus)], check=True, capture_output=True)
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--input", str(corpus / "input"), "--output", str(out)],
        capture_output=True, text=True, env={"RAKSHA_SECRET": "heldout-test-secret", "PATH": "/usr/bin:/bin"},
        cwd=str(REPO),
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return corpus, out


def _score(corpus: Path, out: Path) -> dict:
    """Re-score in-process so the assertions can read structured numbers."""
    import hashlib
    import unicodedata
    from collections import Counter

    def norm(t: str) -> str:
        return unicodedata.normalize("NFC", t or "")

    truth = json.loads((corpus / "truth.json").read_text(encoding="utf-8"))
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    sha_to_rel = {
        hashlib.sha256((corpus / "input" / rel).read_bytes()).hexdigest(): rel
        for rel in truth["files"]
    }
    renamed = {}
    for e in report.get("files") or []:
        rel = sha_to_rel.get(e.get("input_sha256"))
        if rel:
            renamed[rel] = str(e["output"]).replace("sanitized/", "", 1) if e.get("output") else None

    leaks, totals = Counter(), Counter()
    by_channel: Counter = Counter()
    kept = total_preserve = exported = 0
    for rel, f in truth["files"].items():
        out_rel = renamed.get(rel, rel)
        path = (out / "sanitized" / out_rel) if out_rel else None
        exists = bool(path and path.is_file())
        exported += exists
        text = norm(path.read_text(encoding="utf-8", errors="replace")) if exists else ""
        for unit in f["units"].values():
            for e in unit["entities"]:
                totals[e["type"]] += 1
                surface = norm(e["surface"])
                hay = norm(out_rel or "") if e["channel"] == "filename" else text
                if surface and (exists or e["channel"] == "filename") and surface in hay:
                    leaks[e["type"]] += 1
                    by_channel[e["channel"]] += 1
            if exists:
                for pv in unit.get("preserve") or []:
                    total_preserve += 1
                    kept += norm(pv["text"]) in text
    return {
        "leaks": leaks, "totals": totals, "by_channel": by_channel,
        "kept": kept, "total_preserve": total_preserve,
        "exported": exported, "files": len(truth["files"]),
    }


def test_heldout_corpus_shares_no_surface_forms_with_the_practice_data(heldout):
    """A held-out set that overlaps the tuning set proves nothing."""
    if not PRACTICE.is_dir():
        pytest.skip("organizer practice corpus not present")
    corpus, _out = heldout
    practice = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in PRACTICE.rglob("*") if p.is_file()
    )
    truth = json.loads((corpus / "truth.json").read_text(encoding="utf-8"))
    surfaces = {
        e["surface"] for f in truth["files"].values()
        for u in f["units"].values() for e in u["entities"]
    }
    overlap = sorted(s for s in surfaces if len(s) >= 4 and s in practice)
    assert not overlap, f"held-out surfaces also present in the practice data: {overlap}"


def test_no_entity_survives_on_the_heldout_corpus(heldout):
    scored = _score(*heldout)
    leaked = sum(scored["leaks"].values())
    assert leaked == 0, dict(scored["leaks"])


def test_every_scored_channel_is_covered_and_clean(heldout):
    """An aggregate leak rate hides the failure that matters -- one channel at
    100%. Each channel the organizer scores separately is asserted on its own.
    """
    corpus, _out = heldout
    truth = json.loads((corpus / "truth.json").read_text(encoding="utf-8"))
    present = {
        e["channel"] for f in truth["files"].values()
        for u in f["units"].values() for e in u["entities"]
    }
    assert present >= {"content", "filename", "key", "header", "metadata", "base64"}
    scored = _score(corpus, _out)
    assert dict(scored["by_channel"]) == {}, dict(scored["by_channel"])


def test_clinical_values_survive_on_the_heldout_corpus(heldout):
    """Suppressing everything is not a solution. The diagnoses, drug lines and
    measurements in this corpus must all come through."""
    scored = _score(*heldout)
    assert scored["total_preserve"] > 0
    assert scored["kept"] == scored["total_preserve"], (
        f"{scored['total_preserve'] - scored['kept']} clinical values destroyed"
    )


def test_nothing_had_to_be_quarantined_to_achieve_that(heldout):
    """Quarantining a file also stops its PII leaking. These files are all
    well-formed and supported, so every one must be published."""
    scored = _score(*heldout)
    assert scored["exported"] == scored["files"], scored
