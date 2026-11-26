"""Release manifest: safe relative paths + SHA-256 hashes + policy/release
identity. No private evidence, no raw literals -- only what is already
published alongside it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(public_dir: Path, release_id: str, policy_name: str, policy_version: str) -> dict:
    files = []
    for p in sorted(public_dir.rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(public_dir).as_posix()),
                "sha256": sha256_of(p),
                "bytes": p.stat().st_size,
            })
    return {
        "release_id": release_id,
        "policy_name": policy_name,
        "policy_version": policy_version,
        "files": files,
    }


def verify_manifest(public_dir: Path, manifest: dict) -> list[str]:
    """Recomputes hashes of every file actually present and compares against
    the manifest -- used by `validate` to detect tampering after the fact."""
    problems: list[str] = []
    declared = {f["path"]: f for f in manifest.get("files", [])}
    actual_paths = {str(p.relative_to(public_dir).as_posix()) for p in public_dir.rglob("*") if p.is_file() and p.name != "manifest.json"}
    for path in sorted(set(declared) - actual_paths):
        problems.append(f"MANIFEST_FILE_MISSING:{path}")
    for path in sorted(actual_paths - set(declared)):
        problems.append(f"UNDECLARED_FILE_PRESENT:{path}")
    for path in sorted(set(declared) & actual_paths):
        entry = declared[path]
        actual_hash = sha256_of(public_dir / path)
        if actual_hash != entry.get("sha256"):
            problems.append(f"MANIFEST_HASH_MISMATCH:{path}")
        actual_bytes = (public_dir / path).stat().st_size
        if actual_bytes != entry.get("bytes"):
            problems.append(f"MANIFEST_SIZE_MISMATCH:{path}")
    return problems


def load_manifest(public_dir: Path) -> dict | None:
    path = public_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
