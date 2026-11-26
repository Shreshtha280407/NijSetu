"""Plain-text adapter for clinical notes/prescriptions/discharge summaries/reports.

Keeps original offsets and line context so detectors can return exact spans,
and tags coarse section labels (address/OPD/prescription/discharge/...) from
nearby field-label tokens so downstream context rules (C/O vs K/C/O vs H/O,
residential vs hospital ward) have something to anchor on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import FileRecord, Disposition
from .encoding import detect_and_decode

_SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("REGISTRATION", re.compile(r"^\s*(registration|patient\s+details|admission)\b", re.I)),
    ("ADDRESS", re.compile(r"^\s*address\b", re.I)),
    ("OPD", re.compile(r"^\s*(opd|complaint|c/o|presenting)\b", re.I)),
    ("HISTORY", re.compile(r"^\s*(h/o|history|k/?c/?o)\b", re.I)),
    ("PRESCRIPTION", re.compile(r"^\s*(rx|prescription|medication[s]?)\b", re.I)),
    ("DIAGNOSTIC", re.compile(r"^\s*(diagnostic|lab|report|investigation)\b", re.I)),
    ("DISCHARGE", re.compile(r"^\s*discharge\b", re.I)),
    ("WARD", re.compile(r"^\s*ward\b", re.I)),
]


@dataclass
class TxtDocument:
    text: str
    line_starts: list[int]  # char offset of the start of each line
    section_of_line: list[str]  # running section label per line (carries forward)

    def line_no(self, char_offset: int) -> int:
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= char_offset:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def section_at(self, char_offset: int) -> str:
        return self.section_of_line[self.line_no(char_offset)]


@dataclass
class TxtResult:
    file_record: FileRecord
    document: TxtDocument = field(default=None)  # type: ignore[assignment]


def parse_txt(raw: bytes, rel_path: str, file_no: int) -> TxtResult:
    text, _enc = detect_and_decode(raw)
    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    section_of_line: list[str] = []
    offset = 0
    current_section = "GENERAL"
    for line in lines:
        line_starts.append(offset)
        stripped = line.rstrip("\n").rstrip("\r")
        for label, pattern in _SECTION_PATTERNS:
            if pattern.search(stripped):
                current_section = label
                break
        section_of_line.append(current_section)
        offset += len(line)
    if not line_starts:
        line_starts = [0]
        section_of_line = ["GENERAL"]

    doc = TxtDocument(text=text, line_starts=line_starts, section_of_line=section_of_line)
    record_count = 1 if text.strip() else 0
    return TxtResult(
        file_record=FileRecord(file_no, rel_path, "txt", Disposition.PROCESSED, record_count),
        document=doc,
    )
