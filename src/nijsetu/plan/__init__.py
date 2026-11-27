"""Public plan API: run the pipeline and persist its two artifacts."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ..security import SecureStoreError, decrypt_blob, encrypt_blob
from .builder import AnalysisPlan, run_pipeline

__all__ = ["AnalysisPlan", "run_pipeline", "write_plan", "read_private_artifact"]

_PRIVATE_PURPOSE = b"private-analysis"
_PRIVATE_LOGICAL_ID = b"analysis"


def write_plan(plan: AnalysisPlan, output_dir: Path, private_key: bytes) -> tuple[Path, Path]:
    """Writes safe_report (normal perms, plaintext -- it is already PII-free
    by construction) and the private artifact (AES-256-GCM ciphertext,
    0600, own dir) under `private_key` -- a storage-encryption key from a
    cryptographic domain distinct from `phase2.keys`'s HMAC release key.
    Disk compromise alone (Threat A/B in GATE06_SECURITY_FINAL_REPORT.md)
    no longer discloses the evidence/spans this file carries; only holding
    `private_key` does. See `read_private_artifact` for the matching
    authorized reader.

    Returns (safe_report_path, private_artifact_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(plan.safe_report, indent=2, ensure_ascii=False), encoding="utf-8")

    private_dir = output_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(private_dir, stat.S_IRWXU)  # owner-only directory
    private_path = private_dir / "analysis.json"
    plaintext = json.dumps(plan.private, indent=2, ensure_ascii=False).encode("utf-8")
    envelope = encrypt_blob(private_key, _PRIVATE_PURPOSE, _PRIVATE_LOGICAL_ID, plaintext)
    private_path.write_bytes(envelope)
    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner read/write only

    return report_path, private_path


def read_private_artifact(private_path: Path, private_key: bytes) -> dict:
    """Authorized reader for the artifact `write_plan` encrypts. Raises
    `SecureStoreError` (never returns a partial result) on a wrong key, a
    tampered/truncated file, or an unknown envelope version."""
    envelope = private_path.read_bytes()
    plaintext = decrypt_blob(private_key, _PRIVATE_PURPOSE, _PRIVATE_LOGICAL_ID, envelope)
    try:
        return json.loads(plaintext)
    except ValueError as exc:
        raise SecureStoreError("PRIVATE_ARTIFACT_NOT_VALID_JSON") from exc
