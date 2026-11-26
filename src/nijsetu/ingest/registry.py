"""Input discovery: root-boundary enforcement, symlink safety, format detection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".txt": "txt"}


@dataclass
class DiscoveredFile:
    file_no: int
    abs_path: Path
    rel_path: str  # relative to the declared input root; safe to surface
    declared_format: str  # csv / tsv / json / txt / fhir / unknown


def discover_files(input_path: Path) -> list[DiscoveredFile]:
    """Walk a file or directory, staying strictly within its root boundary.

    Symlinks are skipped explicitly (never followed) so a crafted symlink
    cannot escape the declared input root.
    """
    root = input_path.resolve()
    found: list[Path] = []
    if root.is_file():
        found.append(root)
    elif root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in sorted(dirnames) if not Path(dirpath, d).is_symlink()]
            for name in sorted(filenames):
                p = Path(dirpath, name)
                if p.is_symlink():
                    continue
                found.append(p)
    else:
        return []

    out: list[DiscoveredFile] = []
    for i, p in enumerate(found):
        try:
            rel = p.resolve().relative_to(root if root.is_dir() else root.parent)
        except ValueError:
            continue  # escaped the root boundary; refuse to touch it
        out.append(DiscoveredFile(
            file_no=i,
            abs_path=p,
            rel_path=str(rel),
            declared_format=detect_format(p),
        ))
    return out


def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    fmt = SUPPORTED_EXTENSIONS.get(ext)
    if fmt is None:
        return "unknown"
    if fmt == "json":
        if _looks_like_fhir(path):
            return "fhir"
        return "json"
    return fmt


def _looks_like_fhir(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    try:
        text = head.decode("utf-8", errors="ignore")
    except Exception:
        return False
    return '"resourceType"' in text
