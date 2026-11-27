"""Regression tests for the pipe/tab-delimited ".txt" sniffer
(ingest.registry._sniff_delimited_txt) -- a legacy hospital export can be a
bare delimited table under a ".txt" extension. Free-text scanning only
catches PII with an inline label or a gazetteer hit, so a value sitting in a
recognized COLUMN position (a compact DOB, a village name) silently survived
until this file is promoted to be parsed like a real CSV/TSV."""

from __future__ import annotations

from pathlib import Path

from nijsetu.config import AppConfig
from nijsetu.ingest.registry import detect_format
from nijsetu.phase2.policy import BALANCED
from nijsetu.phase2.sanitize import run_sanitize

KEY = b"test-suite-master-secret-key-material-32bytes!!"


def _sanitize(input_dir: Path, out: Path):
    return run_sanitize(input_dir, out, BALANCED, "REL1", KEY, AppConfig())


def test_pipe_delimited_txt_with_resolvable_header_is_promoted(tmp_path):
    p = tmp_path / "legacy.txt"
    p.write_text("ID|NAME|DOB_YYYYMMDD|VILLAGE\nLEG1|Asha Verma|19901203|Rampur\n", encoding="utf-8")
    assert detect_format(p) == "psv"


def test_tab_delimited_txt_with_resolvable_header_is_promoted_to_tsv(tmp_path):
    p = tmp_path / "legacy.txt"
    p.write_text("ID\tNAME\tDOB\n1\tAsha Verma\t1990-12-03\n", encoding="utf-8")
    assert detect_format(p) == "tsv"


def test_ordinary_prose_txt_is_not_misdetected_as_tabular(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text(
        "C/O fever x 3 days. H/O hypertension. Seen by Dr. Ramesh. "
        "Rx: Metformin 500mg 1-0-1. Discharged stable.\n", encoding="utf-8")
    assert detect_format(p) == "txt"


def test_pipe_delimited_dump_with_unrecognized_columns_stays_txt(tmp_path):
    # Consistent pipe structure, but the header doesn't resolve to known
    # roles -- must not be promoted on shape alone.
    p = tmp_path / "data.txt"
    p.write_text("FOO|BAR|BAZ\nqux|quux|corge\n", encoding="utf-8")
    assert detect_format(p) == "txt"


def test_promoted_psv_file_gets_dob_and_village_sanitized(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "legacy_export.txt").write_text(
        "ID|NAME|DOB_YYYYMMDD|VILLAGE\n"
        "LEG001|Asha Verma|19901203|Rampur Khera\n"
        "LEG002|Sunil Rao|19851122|Narhi\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    outcome = _sanitize(input_dir, out)
    assert outcome.status in ("RELEASED", "RELEASED_WITH_HOLDS")
    [released_file] = (out / "released").iterdir()
    text = released_file.read_text(encoding="utf-8")
    assert "19901203" not in text
    assert "Rampur Khera" not in text
    assert "Asha Verma" not in text


def test_duplicate_header_psv_is_quarantined_not_leaked(tmp_path):
    # Same column-identity ambiguity csv_adapter already refuses to guess at
    # for a real .csv -- promoting a lookalike ".txt" must inherit the same
    # safe refusal, not bypass it via free-text fallback.
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "legacy_export.txt").write_text(
        "ID|ID|NAME|NAME|DOB_YYYYMMDD|VILLAGE\n"
        "LEG001|LEG001|Asha Verma|Asha Verma|19901203|Rampur Khera\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    outcome = _sanitize(input_dir, out)
    assert "DUPLICATE_HEADER" in outcome.report["hold_summary"]
