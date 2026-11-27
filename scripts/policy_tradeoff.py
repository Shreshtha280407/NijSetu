#!/usr/bin/env python3
"""Measure the privacy/utility trade-off of each policy knob, end to end.

A privacy policy is a set of choices that each buy privacy with utility, and a
submission that cannot say what each choice cost has not made a choice -- it
has inherited a default. This runs the real Phase 2 pipeline once per policy
variant and reports, for each: how many clinical values survive, how many
records are released, how many files are quarantined, and -- as the privacy
side of the ledger -- how unique the released quasi-identifier combinations are
against the organizer's own population registry.

The uniqueness column is the honest privacy metric: it counts released records
whose surviving attributes (district + age band + gender) match exactly ONE
person in a 10,000-row electoral-roll-style registry. k=1 means that record is
re-identifiable from public data alone, with no help from anything we leaked.

    python scripts/policy_tradeoff.py <input_dir> <registry_csv> <work_dir>
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRUTH = REPO.parent / "Secret-Wards-ToolKit" / "dev" / "truth" / "truth.json"

# (label, policy override written to a JSON file consumed by --policy-file)
VARIANTS: list[tuple[str, dict]] = [
    ("balanced (shipped)", {}),
    ("age band 5y", {"age_band": {"band_width": 5}}),
    ("age band 20y", {"age_band": {"band_width": 20}}),
    ("no date shift", {"date_shift_min_days": 0, "date_shift_max_days": 0}),
    ("date shift +-90d", {"date_shift_min_days": -90, "date_shift_max_days": 90}),
    ("geography: none", {"geography_level": "none"}),
    ("strict preset", {"__preset__": "strict"}),
]


def norm(t: str) -> str:
    return unicodedata.normalize("NFC", t or "")


def load_registry(path: Path) -> Counter:
    """(district, age band, gender) -> how many real people share it."""
    counts: Counter = Counter()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                age = int(row["age"])
            except (KeyError, ValueError):
                continue
            counts[(row.get("district", "").strip().lower(), age // 10 * 10, row.get("gender", "").strip())] += 1
    return counts


def released_cohorts(out: Path) -> list[tuple[str, int, str]]:
    """Quasi-identifier combinations actually published, read back out of the
    released files rather than from any internal structure."""
    cohorts: list[tuple[str, int, str]] = []
    report_path = out / "report.json"
    if not report_path.is_file():
        return cohorts
    for path in sorted((out / "sanitized").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".csv", ".tsv"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
        if not rows:
            continue
        keys = {k.lower().strip(): k for k in rows[0] if k}
        dist_key = next((keys[k] for k in keys if "district" in k or "jila" in k), None)
        age_key = next((keys[k] for k in keys if k in ("age", "umar", "umr")), None)
        sex_key = next((keys[k] for k in keys if k in ("gender", "sex", "ling")), None)
        if not (dist_key and age_key):
            continue
        for row in rows:
            band = (row.get(age_key) or "").strip()
            digits = "".join(c for c in band if c.isdigit())
            if not digits:
                continue
            cohorts.append((
                (row.get(dist_key) or "").strip().lower(),
                int(digits[:3]) // 10 * 10,
                (row.get(sex_key) or "").strip() if sex_key else "",
            ))
    return cohorts


def clinical_kept(out: Path) -> str:
    """Share of the organizer answer key's PRESERVE values still present.

    Computed here rather than by shelling out to check_dev.py, because that
    checker expects the organizer output contract (sanitized/ + report.json)
    and these variants are internal Phase 2 releases.
    """
    truth_path = TRUTH
    if not truth_path.is_file():
        return "n/a"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    blob = "\n".join(
        norm(p.read_text(encoding="utf-8", errors="replace"))
        for p in (out / "sanitized").rglob("*")
        if p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".json", ".txt")
    )
    kept = total = 0
    for f in truth["files"].values():
        for unit in f["units"].values():
            for pv in unit.get("preserve") or []:
                total += 1
                kept += norm(pv["text"]) in blob
    return "n/a" if not total else f"{100.0 * kept / total:.1f}%"


def run_variant(input_dir: Path, work: Path, label: str, override: dict) -> dict:
    """One real end-to-end release under one policy.

    Driven through `python -m nijsetu sanitize`, which already exposes the
    policy knobs (`--policy` preset plus a `--config` JSON override), rather
    than through main.py: the organizer entrypoint deliberately hard-codes the
    shipped preset, and giving it an environment override just so this harness
    could reach it would add a configuration path to the submission that the
    submission itself never uses.
    """
    out = work / label.replace(" ", "_").replace(":", "").replace("+", "p").replace("(", "").replace(")", "")
    shutil.rmtree(out, ignore_errors=True)
    preset = override.pop("__preset__", "balanced")
    policy_config = None
    if override:
        policy_config = work / f"policy_{out.name}.json"
        policy_config.write_text(json.dumps(override), encoding="utf-8")
    sys.path.insert(0, str(REPO / "src"))
    from nijsetu.organizer import build_output
    out.mkdir(parents=True, exist_ok=True)
    code = build_output(input_dir, out, "practice", policy_name=preset, policy_config=policy_config)
    result: dict = {"label": label, "exit": code, "out": out}
    report_path = out / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        statuses = Counter(f["status"] for f in report.get("files") or [])
        result["released"] = statuses.get("sanitized", 0)
        result["quarantined"] = statuses.get("quarantined", 0)
        records = out / "records.jsonl"
        result["records"] = len(records.read_text(encoding="utf-8").strip().splitlines()) if records.is_file() else 0
    return result


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    input_dir, registry_csv, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    registry = load_registry(registry_csv)
    population = sum(registry.values())

    header = f"{'policy variant':<22} {'files':>6} {'quar':>5} {'records':>8} {'clinical':>9} {'k=1':>6} {'k<3':>6}"
    print(header)
    print("-" * len(header))
    for label, override in VARIANTS:
        res = run_variant(input_dir, work, label, dict(override))
        if res["exit"] != 0 or "released" not in res:
            print(f"{label:<22} {'FAILED (exit ' + str(res['exit']) + ')':>40}")
            continue
        kept = clinical_kept(res["out"])
        cohorts = released_cohorts(res["out"])
        unique = sum(1 for c in cohorts if registry.get(c, 0) == 1)
        small = sum(1 for c in cohorts if 0 < registry.get(c, 0) < 3)
        print(f"{label:<22} {res['released']:>6} {res['quarantined']:>5} {res['records']:>8} "
              f"{kept:>9} {unique:>6} {small:>6}")
    print(f"\nregistry population: {population} people in {len(registry)} "
          f"(district, 10y age band, gender) cohorts")
    print("k=1 / k<3 count RELEASED rows whose surviving quasi-identifiers match")
    print("exactly one / fewer than three real people in that registry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
