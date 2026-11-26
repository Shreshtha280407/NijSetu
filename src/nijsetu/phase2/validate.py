"""Standalone `validate` command: checks a published release using only
what that release itself contains -- manifest integrity, structural
validity, FHIR reference resolution, and generic residual-pattern scanning.

Explicitly out of scope here (documented, not silently skipped): a
known-original-identifier check and source-to-output clinical equivalence
both require the PRIVATE source evidence captured during `sanitize`, which
never leaves that run and is not present in a published release directory.
`validate` reports this distinction in its own output rather than implying
a broader guarantee than it can actually check.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .leak_scan import scan_generic_residual_patterns
from .manifest import load_manifest, verify_manifest
from .transform_csv import header_leak_features

SCOPE_NOTE = (
    "validate checks manifest integrity, structure, FHIR references and generic "
    "residual patterns using only this release's own published content. It cannot "
    "verify known-original-identifier absence or source-to-output clinical "
    "equivalence -- both require the private source evidence captured during the "
    "original `sanitize` run, which is not published and not available here."
)


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict]
    scope_note: str = SCOPE_NOTE


def _check_manifest(release_dir: Path) -> tuple[dict, dict | None]:
    manifest = load_manifest(release_dir)
    if manifest is None:
        return {"name": "manifest_present", "passed": False, "detail": "manifest.json missing or unreadable"}, None
    return {"name": "manifest_present", "passed": True, "detail": "manifest.json loaded"}, manifest


def _check_structure(release_dir: Path) -> dict:
    released = release_dir / "released"
    problems: list[str] = []
    if released.is_dir():
        for p in sorted(released.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(release_dir)
            if p.suffix == ".json":
                try:
                    json.loads(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    problems.append(f"{rel}: {exc}")
            elif p.suffix in (".csv", ".tsv"):
                delimiter = "\t" if p.suffix == ".tsv" else ","
                try:
                    text = p.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    problems.append(f"{rel}: {exc}")
                    continue
                rows = list(csv.reader(text.splitlines(), delimiter=delimiter)) if text.strip() else []
                if rows:
                    width = len(rows[0])
                    for i, row in enumerate(rows[1:], start=1):
                        if len(row) != width:
                            problems.append(f"{rel} row {i}: column count mismatch")
    detail = "; ".join(problems) or "all released files parse and are structurally consistent"
    return {"name": "structural_validity", "passed": not problems, "detail": detail}


def _check_fhir_references(release_dir: Path) -> dict:
    released = release_dir / "released"
    problems: list[str] = []
    if released.is_dir():
        for p in sorted(released.rglob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not (isinstance(data, dict) and data.get("resourceType") == "Bundle"):
                continue
            rel = p.relative_to(release_dir)
            ids = {
                entry["resource"]["id"]
                for entry in (data.get("entry") or [])
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict) and entry["resource"].get("id")
            }
            for entry in data.get("entry") or []:
                res = entry.get("resource") if isinstance(entry, dict) else None
                if not isinstance(res, dict):
                    continue
                for key in ("subject", "encounter", "performer"):
                    ref = res.get(key)
                    reference = ref.get("reference", "") if isinstance(ref, dict) else ""
                    if reference.startswith("urn:uuid:") and reference[len("urn:uuid:"):] not in ids:
                        problems.append(f"{rel}: unresolved {key} reference")
    detail = "; ".join(problems) or "all FHIR references resolve within their bundle"
    return {"name": "fhir_reference_resolution", "passed": not problems, "detail": detail}


def _check_headers(release_dir: Path) -> dict:
    """Independent re-scan of every released CSV/TSV header row -- does not
    trust transform_csv's own bookkeeping, re-derives from the actual
    exported file, same detectors used at sanitize time."""
    released = release_dir / "released"
    problems: list[str] = []
    if released.is_dir():
        for p in sorted(released.rglob("*")):
            if not p.is_file() or p.suffix not in (".csv", ".tsv"):
                continue
            delimiter = "\t" if p.suffix == ".tsv" else ","
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rows = list(csv.reader(text.splitlines(), delimiter=delimiter)) if text.strip() else []
            if not rows:
                continue
            rel = p.relative_to(release_dir)
            for i, h in enumerate(rows[0]):
                if header_leak_features(h):
                    problems.append(f"{rel} header[{i}]: possible identifying header text")
    detail = "; ".join(problems) or "no identifying header text detected"
    return {"name": "no_identifying_header_text", "passed": not problems, "detail": detail}


def _check_report_status(release_dir: Path) -> dict:
    """The published report.json must carry a final public status consistent
    with hold_index.json -- a status missing entirely, or disagreeing with
    whether any record was held, must block publication rather than survive
    it (see PHASE2_REPORT.md Gap 5)."""
    report_path = release_dir / "report.json"
    hold_index_path = release_dir / "hold_index.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"name": "report_status_consistent", "passed": False, "detail": f"report.json unreadable: {exc}"}
    try:
        hold_index = json.loads(hold_index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"name": "report_status_consistent", "passed": False, "detail": f"hold_index.json unreadable: {exc}"}

    status = report.get("status")
    if status not in ("RELEASED", "RELEASED_WITH_HOLDS"):
        return {"name": "report_status_consistent", "passed": False, "detail": f"missing or invalid published status: {status!r}"}
    expected = "RELEASED_WITH_HOLDS" if hold_index else "RELEASED"
    if status != expected:
        return {
            "name": "report_status_consistent", "passed": False,
            "detail": f"status={status!r} but hold_index has {len(hold_index)} entries (expected {expected!r})",
        }
    return {"name": "report_status_consistent", "passed": True, "detail": f"status={status!r} agrees with hold_index"}


def validate_release(release_dir: Path) -> ValidationResult:
    checks: list[dict] = []
    manifest_check, manifest = _check_manifest(release_dir)
    checks.append(manifest_check)
    if manifest is not None:
        tamper = verify_manifest(release_dir, manifest)
        checks.append({"name": "manifest_integrity", "passed": not tamper, "detail": "; ".join(tamper) or "all hashes match"})

    checks.append(_check_structure(release_dir))
    checks.append(_check_fhir_references(release_dir))
    checks.append(_check_headers(release_dir))
    checks.append(_check_report_status(release_dir))

    residual = scan_generic_residual_patterns(release_dir)
    checks.append({"name": "no_residual_direct_identifier_patterns", "passed": not residual, "detail": "; ".join(residual) or "clean"})

    return ValidationResult(passed=all(c["passed"] for c in checks), checks=checks)
