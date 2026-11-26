"""Evaluator self-tests (Finding 5): the quality-evaluation logic in
scripts/eval_gate1.py must itself fail loudly when the thing it is supposed
to detect actually happens -- a false positive, a missed span, a wrong
boundary, a merged/split patient, an incomplete group. These are unit tests
of the EVALUATOR, not of the pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval_gate1 import GoldSpan, entity_metrics  # noqa: E402

ANNOTATED = {("f.txt", "PATIENT_NAME")}


def _rec(file_path: str, record_no: int, feats: list[tuple[str, int, int]]) -> dict:
    return {
        "file_path": file_path,
        "record_key": f"0:{record_no}",
        "features": [{"role": role, "char_start": s, "char_end": e} for role, s, e in feats],
    }


# --- entity_metrics self-tests ----------------------------------------------

def test_injected_false_positive_decreases_precision():
    gold = [GoldSpan("f.txt", "PATIENT_NAME", "Arif", "freetext", "en", char_start=0, char_end=4)]
    clean = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 4)])]
    with_fp = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 4), ("PATIENT_NAME", 10, 16)])]
    # strict=False: this scenario deliberately makes detection disagree with
    # the "fully annotated" gold count -- that is the false positive under
    # test, not a gold-data bug the self-check (tested separately below) is
    # meant to catch.
    clean_metrics = entity_metrics(clean, gold, ANNOTATED, strict=False)
    fp_metrics = entity_metrics(with_fp, gold, ANNOTATED, strict=False)
    assert clean_metrics["precision_in_annotated_scope"] == 1.0
    assert fp_metrics["precision_in_annotated_scope"] < clean_metrics["precision_in_annotated_scope"]
    assert fp_metrics["false_positives_in_annotated_scope"] == 1


def test_removed_true_span_decreases_recall():
    gold = [
        GoldSpan("f.txt", "PATIENT_NAME", "Arif", "freetext", "en", char_start=0, char_end=4),
        GoldSpan("f.txt", "PATIENT_NAME", "Meera", "freetext", "en", char_start=10, char_end=15),
    ]
    full = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 4), ("PATIENT_NAME", 10, 15)])]
    missing_one = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 4)])]
    full_metrics = entity_metrics(full, gold, set())
    partial_metrics = entity_metrics(missing_one, gold, set())
    assert partial_metrics["recall_overlap"] < full_metrics["recall_overlap"]
    assert partial_metrics["false_negatives"] == 1


def test_shifted_span_boundary_diverges_exact_from_overlap():
    gold = [GoldSpan("f.txt", "PATIENT_NAME", "Arif", "freetext", "en", char_start=0, char_end=4)]
    shifted = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 5)])]  # one char too wide, still overlaps
    metrics = entity_metrics(shifted, gold, set())
    assert metrics["true_positives_exact"] == 0
    assert metrics["true_positives_overlap"] == 1
    assert metrics["recall_exact"] < metrics["recall_overlap"]


def test_repeated_identical_text_in_different_records_is_not_cross_satisfied():
    """Two records both contain a PATIENT_NAME span with the exact same
    text ("Ramesh") at the exact same local offset -- the gold span scoped
    to record 1 must only match record 1's span, never record 0's."""
    gold = [GoldSpan("f.txt", "PATIENT_NAME", "Ramesh", "freetext", "en", record_no=1, char_start=0, char_end=6)]
    records = [
        _rec("f.txt", 0, [("PATIENT_NAME", 0, 6)]),
        _rec("f.txt", 1, [("PATIENT_NAME", 0, 6)]),
    ]
    metrics = entity_metrics(records, gold, set())
    assert metrics["true_positives_exact"] == 1
    assert metrics["false_negatives"] == 0


def test_one_predicted_span_cannot_satisfy_two_gold_spans():
    gold = [
        GoldSpan("f.txt", "PATIENT_NAME", "Ramesh", "freetext", "en", char_start=0, char_end=6),
        GoldSpan("f.txt", "RELATIVE_NAME", "Ramesh", "freetext", "en", char_start=0, char_end=6),
    ]
    # Only ONE predicted span exists, and only under PATIENT_NAME -- it must
    # satisfy at most one gold entry (the matching-role one), never both.
    records = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 6)])]
    metrics = entity_metrics(records, gold, set())
    assert metrics["true_positives_overlap"] == 1
    assert metrics["false_negatives"] == 1


def test_annotated_scope_consistency_check_fails_when_gold_is_out_of_sync():
    gold = [GoldSpan("f.txt", "PATIENT_NAME", "Arif", "freetext", "en", char_start=0, char_end=4)]
    records = [_rec("f.txt", 0, [("PATIENT_NAME", 0, 4), ("PATIENT_NAME", 10, 16)])]
    try:
        entity_metrics(records, gold, {("f.txt", "PATIENT_NAME")})
    except AssertionError:
        return
    raise AssertionError("expected the fully-annotated-scope consistency check to fail")


# --- link_metrics self-tests (via a minimal private-artifact shape) --------

def _private(records: list[dict]) -> dict:
    return {"records": records}


_file_no_counter = [0]


def _link_rec(file_path: str, record_no: int, status: str, cluster: str | None, file_no: int | None = None) -> dict:
    if file_no is None:
        _file_no_counter[0] += 1
        file_no = _file_no_counter[0]
    return {"file_path": file_path, "record_key": f"{file_no}:{record_no}", "link_status": status, "cluster_id": cluster, "reason_codes": []}


def test_merging_two_different_patients_decreases_linking_precision(monkeypatch):
    import eval_gate1 as eg
    monkeypatch.setattr(eg, "GOLD_CLUSTERS", {})
    monkeypatch.setattr(eg, "GOLD_NON_LINKS", [("a.csv", "b.csv")])
    separate = _private([_link_rec("a.csv", 0, "SINGLETON", "0:0", 10), _link_rec("b.csv", 0, "SINGLETON", "0:1", 11)])
    merged = _private([_link_rec("a.csv", 0, "ACCEPTED", "0:0", 20), _link_rec("b.csv", 0, "ACCEPTED", "0:0", 21)])
    separate_metrics = eg.link_metrics(separate)
    merged_metrics = eg.link_metrics(merged)
    assert separate_metrics["incorrect_merges"] == 0
    assert merged_metrics["incorrect_merges"] == 1
    assert (merged_metrics["pairwise_precision_over_sampled_negatives"] or 0) < (
        separate_metrics["pairwise_precision_over_sampled_negatives"] or 1
    )


def test_splitting_one_patient_decreases_linking_recall(monkeypatch):
    import eval_gate1 as eg
    monkeypatch.setattr(eg, "GOLD_CLUSTERS", {"g": ["a.csv", "b.csv"]})
    monkeypatch.setattr(eg, "GOLD_NON_LINKS", [])
    linked = _private([_link_rec("a.csv", 0, "ACCEPTED", "0:0", 30), _link_rec("b.csv", 0, "ACCEPTED", "0:0", 31)])
    split = _private([_link_rec("a.csv", 0, "ACCEPTED", "0:0", 32), _link_rec("b.csv", 0, "SINGLETON", "0:1", 33)])
    linked_metrics = eg.link_metrics(linked)
    split_metrics = eg.link_metrics(split)
    assert linked_metrics["pairwise_recall"] == 1.0
    assert split_metrics["pairwise_recall"] < linked_metrics["pairwise_recall"]
    assert split_metrics["pairwise_missed_links"] == 1


def test_omitting_one_group_record_fails_full_group_success(monkeypatch):
    import eval_gate1 as eg
    monkeypatch.setattr(eg, "GOLD_CLUSTERS", {"g": ["a.csv", "b.csv", "c.csv"]})
    monkeypatch.setattr(eg, "GOLD_NON_LINKS", [])
    # c.csv is HELD, not ACCEPTED -- the group must not read as fully linked
    # just because a and b (filtered to ACCEPTED only) happen to agree.
    partial = _private([
        _link_rec("a.csv", 0, "ACCEPTED", "0:0", 40),
        _link_rec("b.csv", 0, "ACCEPTED", "0:0", 41),
        _link_rec("c.csv", 0, "HELD", None, 42),
    ])
    metrics = eg.link_metrics(partial)
    assert metrics["groups_fully_linked"] == 0
    assert metrics["group_detail"][0]["fully_linked"] is False


def test_two_csv_rows_in_one_file_are_evaluated_independently(monkeypatch):
    """The original bug: indexing by file_path alone overwrote one of two
    rows from the same CSV file. record_key-based resolution must keep
    both independently addressable via the "file#record_no" spec."""
    import eval_gate1 as eg
    monkeypatch.setattr(eg, "GOLD_CLUSTERS", {})
    monkeypatch.setattr(eg, "GOLD_NON_LINKS", [("family.csv#0", "family.csv#1")])
    private = _private([
        _link_rec("family.csv", 0, "ACCEPTED", "0:0", 50),
        _link_rec("family.csv", 1, "ACCEPTED", "0:0", 50),
    ])
    metrics = eg.link_metrics(private)
    # Both rows resolved to distinct records and the (incorrect) merge was detected.
    assert metrics["incorrect_merges"] == 1
