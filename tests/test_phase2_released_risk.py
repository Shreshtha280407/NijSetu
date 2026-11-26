"""Risk groups must describe surviving exported values, including shifted months."""

import csv
import json
from collections import Counter

import pytest

from nijsetu.config import AppConfig
from nijsetu.phase2 import keys
from nijsetu.phase2.policy import BALANCED
from nijsetu.phase2.sanitize import run_sanitize
from nijsetu.phase2.validate import validate_release


@pytest.mark.parametrize("fmt", ["csv", "json", "txt", "fhir"])
def test_risk_groups_follow_shifted_export_months(tmp_path, monkeypatch, fmt):
    monkeypatch.setattr(keys, "date_shift_days", lambda _key, cluster, _lo, _hi: -23 if "0:0" in cluster else -1)
    inp = tmp_path / "input"
    inp.mkdir()
    if fmt == "csv":
        (inp / "records.csv").write_text("UHID,Admission Date\nA01,2024-10-12\nB02,2024-10-12\n")
    elif fmt == "json":
        (inp / "records.json").write_text(json.dumps([
            {"uhid": "A01", "admission_date": "2024-10-12"},
            {"uhid": "B02", "admission_date": "2024-10-12"},
        ]))
    elif fmt == "txt":
        for i, uhid in enumerate(("A01", "B02")):
            (inp / f"record{i}.txt").write_text(f"UHID: {uhid}\nAdmission Date: 2024-10-12\n")
    else:
        (inp / "bundle.json").write_text(json.dumps({
            "resourceType": "Bundle", "type": "collection", "entry": [
                {"resource": {"resourceType": "Patient", "id": "a", "birthDate": "1975-02-14"}},
                {"resource": {"resourceType": "Encounter", "id": "ea", "status": "finished",
                              "subject": {"reference": "Patient/a"}, "period": {"start": "2024-10-12"}}},
                {"resource": {"resourceType": "Patient", "id": "b", "birthDate": "1975-02-14"}},
                {"resource": {"resourceType": "Encounter", "id": "eb", "status": "finished",
                              "subject": {"reference": "Patient/b"}, "period": {"start": "2024-10-12"}}},
            ],
        }))
    out = tmp_path / "release"
    outcome = run_sanitize(inp, out, BALANCED, "MONTH_BOUNDARY", b"synthetic-regression-key-material", AppConfig())
    assert outcome.status == "RELEASED", outcome.report
    dates = []
    for path in (out / "released").iterdir():
        if fmt == "csv":
            with path.open(newline="") as stream:
                dates.extend(row["Admission Date"] for row in csv.DictReader(stream))
        elif fmt == "json":
            dates.extend(row["admission_date"] for row in json.loads(path.read_text()))
        elif fmt == "txt":
            dates.extend(line.split(": ", 1)[1] for line in path.read_text().splitlines() if line.startswith("Admission Date:"))
        else:
            dates.extend(entry["resource"]["period"]["start"] for entry in json.loads(path.read_text())["entry"]
                         if entry["resource"]["resourceType"] == "Encounter")
    assert sorted(dates) == ["2024-09-19", "2024-10-11"]
    expected = dict(Counter(str((None, None, value[:7])) for value in dates))
    after = outcome.report["risk_after"]
    assert after["total_patients"] == 2
    assert after["group_sizes"] == expected
    assert after["singleton_count"] == 2
    assert after["singleton_rate"] == 1.0
    assert outcome.report["risk_before"]["singleton_count"] == 0
    assert json.loads((out / "report.json").read_text())["risk_after"] == after
    assert validate_release(out).passed


def test_partial_hold_uses_surviving_dates_and_counts_patient_once(tmp_path, monkeypatch):
    import nijsetu.phase2.sanitize as sanitize_module

    monkeypatch.setattr(keys, "date_shift_days", lambda *_args: 0)
    original_compare = sanitize_module.compare_assertions

    def hold_earliest(original, staged, offset):
        dates = dict(original.dates)
        if dates.get("admission_date") and dates["admission_date"].month == 9:
            return ["INJECTED_RECORD_HOLD"]
        return original_compare(original, staged, offset)

    monkeypatch.setattr(sanitize_module, "compare_assertions", hold_earliest)
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "records.csv").write_text(
        "UHID,DOB,Admission Date\nA01,1975-02-14,2024-09-01\nA01,,2024-10-12\nA01,,2024-11-12\n"
    )
    out = tmp_path / "release"
    outcome = run_sanitize(inp, out, BALANCED, "PARTIAL_HOLD", b"synthetic-regression-key-material", AppConfig())
    assert outcome.status == "RELEASED_WITH_HOLDS"
    counts = outcome.report["counts"]
    assert counts["records_released"] == 2 and counts["records_held"] == 1
    assert counts["patients_released"] == counts["patients_partially_held"] == 1
    after = outcome.report["risk_after"]
    assert after["total_patients"] == 1
    assert after["group_sizes"] == {str((None, None, "2024-10")): 1}
    assert after["qi_coverage"]["age_band"] == 0.0
    assert outcome.report["risk_before"]["qi_coverage"]["age_band"] == 1.0
    assert validate_release(out).passed
