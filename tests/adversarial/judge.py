#!/usr/bin/env python3
"""Local judge harness: runs the real NijSetu pipeline (python main.py) against
one generated adversarial split and scores the actual exported bytes against
that split's truth.json. Mirrors the organizer's own check_dev.py methodology
(whole-file normalized substring matching) so results are directly comparable
in spirit -- but this is a PROXY, not the official scorer: the real evaluator
has its own hidden registry/attacker/seeds this harness cannot reproduce.

Usage:
    python3 judge.py --split corpora/dev --secret devsecret1
    python3 judge.py --split corpora/dev --secret devsecret1 --secret2 devsecret2  # + determinism/rotation check
"""
from __future__ import annotations

import argparse
import base64
import binascii
import itertools
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_INDIC_DIGITS = "०१२३४५६७८९"
_INDIC_TO_ASCII = {d: str(i) for i, d in enumerate(_INDIC_DIGITS)}
_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = _ZERO_WIDTH.sub("", s)
    s = "".join(_INDIC_TO_ASCII.get(c, c) for c in s)
    return " ".join(s.casefold().split())


def with_decoded_payloads(text: str) -> str:
    extra = []
    for m in _B64.finditer(text):
        tok = m.group(0)
        try:
            extra.append(base64.b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8", errors="ignore"))
        except (binascii.Error, ValueError):
            pass
    try:
        import urllib.parse
        extra.append(urllib.parse.unquote(text))
    except Exception:
        pass
    try:
        import html
        extra.append(html.unescape(text))
    except Exception:
        pass
    return text + "\n" + "\n".join(extra) if extra else text


def read_text(p: Path) -> str:
    try:
        raw = p.read_bytes()[: 50 * 1024 * 1024]
    except OSError:
        return ""
    for enc in ("utf-8", "utf-16", "cp1252"):
        try:
            return with_decoded_payloads(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return with_decoded_payloads(raw.decode("utf-8", errors="replace"))


def all_text_under(root: Path) -> str:
    parts = []
    if not root.is_dir():
        return ""
    for dp, _, fn in os.walk(root):
        for n in fn:
            parts.append(n)
            parts.append(read_text(Path(dp) / n))
    return norm("\n".join(parts))


def run_pipeline(input_dir: Path, output_dir: Path, secret: str) -> dict:
    env = dict(os.environ)
    env["RAKSHA_SECRET"] = secret
    t0 = time.monotonic()
    proc = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "python"), str(REPO_ROOT / "main.py"),
         "--input", str(input_dir), "--output", str(output_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600, env=env,
    )
    elapsed = time.monotonic() - t0
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "elapsed_s": elapsed}


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def score(split_dir: Path, output_dir: Path, run_meta: dict) -> dict:
    truth = json.loads((split_dir / "truth.json").read_text(encoding="utf-8"))
    san = output_dir / "sanitized"

    try:
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    by_sha = {e.get("input_sha256"): e for e in report.get("files") or []}

    recs_by_file: dict[str, dict] = defaultdict(dict)
    try:
        for line in (output_dir / "records.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs_by_file[str(r["file"]).replace("sanitized/", "", 1)][r["loc"]] = r.get("patient")
    except (OSError, ValueError, KeyError):
        pass

    # The output filename actually used for PII-bearing-filename detection --
    # NOT the truth's input-side relative path, since a safe implementation
    # may legitimately rename a PII-bearing path (e.g. "_renamed/file_N.json").
    output_filenames_norm = norm(" ".join(
        p.name for p in san.rglob("*") if p.is_file()
    )) if san.is_dir() else ""

    leaks, kept = Counter(), Counter()
    by_script_leak, by_script_tot = Counter(), Counter()
    by_channel_leak, by_channel_tot = Counter(), Counter()
    leak_examples = []
    pres_ok = pres_all = 0
    unit_patient: dict[tuple, tuple] = {}
    quarantine_failures = []
    raw_copy_failures = []

    for rel, f in truth["files"].items():
        src = split_dir / "input" / rel
        entry = by_sha.get(_sha256_file(src)) if src.is_file() else None
        declared_out = entry.get("output") if entry else None
        p = (output_dir / declared_out) if declared_out else (san / rel)
        exists = p.is_file()
        text = norm(read_text(p)) if exists else ""

        if f.get("has_pii"):
            if entry is None:
                quarantine_failures.append(f"{rel} (not referenced in report.json at all -- silently dropped)")
            elif entry.get("status") == "sanitized":
                if exists and src.read_bytes() == p.read_bytes():
                    raw_copy_failures.append(rel)
            elif entry.get("status") != "quarantined":
                quarantine_failures.append(f"{rel} (report status={entry.get('status')!r}, neither sanitized nor quarantined)")

        for loc, u in f["units"].items():
            for e in u["entities"]:
                by_script_tot[e["script"]] += 1
                by_channel_tot[e["channel"]] += 1
                surface = norm(e["surface"])
                hay = output_filenames_norm if e["channel"] == "filename" else text
                hit = bool(surface) and (exists or e["channel"] == "filename") and surface in hay
                if hit:
                    leaks[e["type"]] += 1
                    by_script_leak[e["script"]] += 1
                    by_channel_leak[e["channel"]] += 1
                    if len(leak_examples) < 25:
                        leak_examples.append((e["type"], e["script"], e["channel"], rel, e["surface"]))
                else:
                    kept[e["type"]] += 1
            if exists:
                for pv in u.get("preserve") or []:
                    pres_all += 1
                    pres_ok += int(norm(pv["text"]) in text)
            if u.get("link") == "required" and u.get("patient"):
                unit_patient[(rel, loc)] = (u["patient"], recs_by_file.get(rel, {}).get(loc))

    everything = all_text_under(output_dir)
    canary_surfaces = {norm(c["surface"]) for c in truth["canaries"]}
    canary_hits = [s for s in canary_surfaces if s and s in everything]

    units = list(unit_patient.values())
    tp = fp = 0
    by_true = defaultdict(list)
    for i, (t, _y) in enumerate(units):
        by_true[t].append(i)
    yours = defaultdict(list)
    for i, (_t, y) in enumerate(units):
        if y:
            yours[y].append(i)
    true_pairs = sum(len(v) * (len(v) - 1) // 2 for v in by_true.values())
    false_merge_examples = []
    for members in yours.values():
        for i, j in itertools.combinations(members, 2):
            if units[i][0] == units[j][0]:
                tp += 1
            else:
                fp += 1
                false_merge_examples.append((units[i][0], units[j][0]))
    fn = true_pairs - tp
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f05 = (1.25 * prec * rec / (0.25 * prec + rec)) if prec + rec else 0.0

    total_ent = sum(leaks.values()) + sum(kept.values())
    return {
        "run": run_meta,
        "leak_rate_overall": (sum(leaks.values()) / total_ent) if total_ent else 0.0,
        "leaks_by_type": dict(leaks), "kept_by_type": dict(kept),
        "leak_by_script": {s: f"{by_script_leak[s]}/{by_script_tot[s]}" for s in by_script_tot},
        "leak_by_channel": {c: f"{by_channel_leak[c]}/{by_channel_tot[c]}" for c in by_channel_tot},
        "leak_examples": leak_examples,
        "clinical_preserved": f"{pres_ok}/{pres_all}",
        "clinical_preserve_rate": (pres_ok / pres_all) if pres_all else None,
        "canary_hits": canary_hits, "canaries_total": len(canary_surfaces),
        "identity_precision": prec, "identity_recall": rec, "identity_f05": f05,
        "false_merge_pairs": len(false_merge_examples), "false_merge_examples": false_merge_examples[:10],
        "quarantine_failures": quarantine_failures, "raw_copy_failures": raw_copy_failures,
        "report_counts": report.get("counts"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, help="path to a corpus dir containing input/ and truth.json")
    ap.add_argument("--secret", default="adversarial-secret-1")
    ap.add_argument("--secret2", default=None, help="if given, also checks pseudonym/date-shift rotation")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    split_dir = Path(a.split).resolve()
    out_dir = Path(a.out) if a.out else split_dir / "output"
    run_meta = run_pipeline(split_dir / "input", out_dir, a.secret)
    result = score(split_dir, out_dir, run_meta)

    if a.secret2:
        out_dir2 = split_dir / "output_secret2"
        run_meta2 = run_pipeline(split_dir / "input", out_dir2, a.secret2)
        san1, san2 = out_dir / "sanitized", out_dir2 / "sanitized"
        files1 = sorted(p.relative_to(san1) for p in san1.rglob("*") if p.is_file()) if san1.is_dir() else []
        files2 = sorted(p.relative_to(san2) for p in san2.rglob("*") if p.is_file()) if san2.is_dir() else []
        differs = files1 != files2 or any((san1 / f).read_bytes() != (san2 / f).read_bytes() for f in files1 if (san2 / f).is_file())
        result["rotation_check"] = {"different_secret_changed_output": differs}

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
