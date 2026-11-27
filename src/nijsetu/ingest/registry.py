"""Input discovery: root-boundary enforcement, symlink safety, format detection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .encoding import detect_and_decode
from .header_aliases import resolve_header

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
    if fmt == "txt":
        sniffed = _sniff_delimited_txt(path)
        if sniffed:
            return sniffed
    return fmt


def _sniff_delimited_txt(path: Path) -> str | None:
    """A ".txt" export can actually be a bare delimited table -- a legacy
    hospital dump with a header row but the wrong extension (see
    docs/DATASET.md's "legacy export formats" trap). Free-text scanning
    only catches PII that carries an inline label or a gazetteer hit; a
    value sitting in a recognized COLUMN position carries neither, so an
    unpromoted file like this silently leaks whatever free-text scanning
    cannot catch on its own (seen directly: compact DOB/village columns).

    Promoted only on unambiguous evidence -- every one of several lines
    splits into the SAME field count (>=3) on one consistent delimiter,
    AND the header resolves at least two known field roles. Comma is
    deliberately excluded (too common in ordinary prose/addresses to be a
    safe signal); ordinary free text essentially never satisfies both
    checks on tab or pipe.
    """
    try:
        text, _enc = detect_and_decode(path.read_bytes()[:65536])
    except Exception:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if len(lines) < 2:
        return None
    for delimiter, fmt in (("\t", "tsv"), ("|", "psv")):
        split_lines = [ln.split(delimiter) for ln in lines]
        n_cols = len(split_lines[0])
        if n_cols < 3 or any(len(cols) != n_cols for cols in split_lines):
            continue
        resolved = sum(1 for h in split_lines[0] if resolve_header(h)[0] is not None)
        if resolved >= 2:
            return fmt
    return None


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
