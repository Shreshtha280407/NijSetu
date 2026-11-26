"""report.json's counts must reconcile, reconstructed from the released bytes.

Gate 0's report claimed `records_quarantined: 0` while eleven files were in
fact quarantined, and `records_processed` summed only released and suppressed
records -- so the records inside a quarantined file were absent from every
total and a reader could not tell whether they had been published, suppressed,
or silently dropped. A self-reported count that nothing checks is a claim, not
evidence.

These tests recompute each total from the output tree itself (the files on
disk, the lines of records.jsonl) rather than from any internal structure, so
they fail if the report and the release ever disagree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nijsetu.organizer import build_output

PRACTICE = Path("/home/Shreshtha/documents/Secret-Wards-ToolKit/dev/input")


@pytest.fixture(scope="module")
def release(tmp_path_factory) -> tuple[Path, dict, list[dict]]:
    if not PRACTICE.is_dir():
        pytest.skip("organizer practice corpus not present")
    out = tmp_path_factory.mktemp("accounting") / "out"
    out.mkdir(parents=True)
    assert build_output(PRACTICE, out, "practice-accounting") == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    claims = [json.loads(ln) for ln in (out / "records.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    return out, report, claims


def test_file_totals_match_the_files_on_disk(release):
    out, report, _claims = release
    counts = report["counts"]
    on_disk = sum(1 for p in (out / "sanitized").rglob("*") if p.is_file())
    assert counts["files_sanitized"] == on_disk
    assert counts["files_total"] == counts["files_sanitized"] + counts["files_quarantined"]
    assert counts["files_total"] == sum(1 for p in PRACTICE.rglob("*") if p.is_file())


def test_every_input_file_is_accounted_for_exactly_once(release):
    """One entry per input FILE, each with either an output path or a
    quarantine reason -- never neither, never both.

    Counted per file rather than per hash on purpose. The organizer's report
    schema identifies an input by `input_sha256`, and the practice corpus
    contains a byte-identical duplicate pair ("sanjeevani_registration.csv"
    and "sanjeevani_registration (1).csv"), so the hash is NOT a unique key
    for an input. Both copies still get their own entry and their own output
    -- dropping one would under-report the release -- but any consumer that
    builds a `{sha: output}` dictionary from this list will resolve one of the
    pair to the other's output. That ambiguity is in the schema, not in this
    release; it is recorded here so the duplicate entries are never "fixed"
    by deduplicating them.
    """
    out, report, _claims = release
    declared = report["files"]
    on_disk = [p for p in PRACTICE.rglob("*") if p.is_file()]
    assert len(declared) == len(on_disk), "every input file needs its own entry"
    assert sorted(e["input_sha256"] for e in declared) == sorted(
        hashlib.sha256(p.read_bytes()).hexdigest() for p in on_disk
    )
    for entry in declared:
        if entry["status"] == "sanitized":
            assert entry.get("output"), entry
            assert (out / str(entry["output"])).is_file(), entry
        else:
            assert entry.get("output") is None, entry
            assert entry.get("reason"), "a quarantine with no reason is not accounted for"


def test_record_totals_reconcile(release):
    """The identity the report now has to satisfy."""
    counts = release[1]["counts"]
    assert counts["records_processed"] == (
        counts["records_released"] + counts["records_suppressed"] + counts["records_quarantined"]
    ), counts


def test_records_quarantined_is_not_silently_zero(release):
    """The specific Gate 0 defect: quarantined files do contain records."""
    counts = release[1]["counts"]
    if counts["files_quarantined"]:
        assert counts["records_quarantined"] > 0, counts


def test_published_claims_count_matches_records_jsonl(release):
    _out, report, claims = release
    assert report["counts"]["record_claims_published"] == len(claims)


def test_claims_and_released_records_differ_only_by_multi_record_documents(release):
    """`record_claims_published` <= `records_released`, because one claim can
    address a document holding several records (a FHIR Bundle) but never the
    reverse. An inversion would mean a claim exists for a record the policy
    engine never released."""
    counts = release[1]["counts"]
    assert counts["record_claims_published"] <= counts["records_released"], counts


def test_every_claim_points_at_a_file_that_was_actually_published(release):
    out, _report, claims = release
    for claim in claims:
        target = claim.get("out") or claim["file"]
        assert (out / str(target)).is_file(), claim


def test_linked_record_count_matches_the_claims_carrying_a_pseudonym(release):
    _out, report, claims = release
    assert report["counts"]["records_linked"] == sum(1 for c in claims if c.get("patient") is not None)


def test_no_quarantined_file_leaves_a_record_claim_behind(release):
    """A quarantined file publishes nothing, so it must not be referenced by
    any claim -- otherwise the claim advertises a file the evaluator cannot
    open."""
    _out, report, claims = release
    published = {str(e["output"]) for e in report["files"] if e.get("output")}
    for claim in claims:
        target = "sanitized/" + str(claim.get("out") or claim["file"]).removeprefix("sanitized/")
        assert target in published, claim
