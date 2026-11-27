"""CSV/TSV streaming adapter. Preserves column order, original text, and leading zeros."""

from __future__ import annotations

import csv
from dataclasses import dataclass

from ..schema import Field, FileRecord, SourceCoordinate, ValueType, Disposition
from .encoding import detect_and_decode
from .header_aliases import normalize_header, resolve_header


@dataclass
class CsvResult:
    file_record: FileRecord
    rows: list[list[Field]]  # one list of Fields per accepted row
    held_rows: list[tuple[int, list[str]]]  # (record_no, reason_codes) for malformed rows


_DELIMITERS = {"tsv": "\t", "psv": "|"}


def parse_csv(raw: bytes, rel_path: str, file_no: int, declared_format: str) -> CsvResult:
    delimiter = _DELIMITERS.get(declared_format, ",")
    text, _enc = detect_and_decode(raw)
    if text.strip() == "":
        return CsvResult(
            file_record=FileRecord(file_no, rel_path, declared_format, Disposition.PROCESSED, 0),
            rows=[],
            held_rows=[],
        )

    reader = csv.reader(text.splitlines(keepends=True), delimiter=delimiter)
    rows_raw = list(reader)
    if not rows_raw:
        return CsvResult(
            file_record=FileRecord(file_no, rel_path, declared_format, Disposition.PROCESSED, 0),
            rows=[],
            held_rows=[],
        )

    header = rows_raw[0]
    norm_headers = [normalize_header(h) for h in header]
    duplicate_raw = len(norm_headers) != len(set(norm_headers))
    file_reasons: list[str] = []
    if duplicate_raw:
        file_reasons.append("DUPLICATE_HEADER")

    # Detect ambiguous alias collisions: two distinct raw headers mapping to
    # the same canonical role. We must not silently guess which one is real.
    canonical_hits: dict[str, list[int]] = {}
    resolved_cols: list[tuple[str | None, object]] = []
    for col_idx, h in enumerate(header):
        canonical, role = resolve_header(h)
        resolved_cols.append((canonical, role))
        if canonical:
            canonical_hits.setdefault(canonical, []).append(col_idx)
    ambiguous_cols = {idx for idxs in canonical_hits.values() if len(idxs) > 1 for idx in idxs}
    if ambiguous_cols:
        file_reasons.append("AMBIGUOUS_HEADER_MAPPING")

    if duplicate_raw:
        # Row shape cannot be safely attributed to columns; hold the whole file.
        return CsvResult(
            file_record=FileRecord(
                file_no, rel_path, declared_format, Disposition.HELD, len(rows_raw) - 1,
                reason_codes=file_reasons,
            ),
            rows=[],
            held_rows=[(i, file_reasons) for i in range(len(rows_raw) - 1)],
        )

    rows: list[list[Field]] = []
    held_rows: list[tuple[int, list[str]]] = []
    for record_no, raw_row in enumerate(rows_raw[1:]):
        if len(raw_row) != len(header):
            held_rows.append((record_no, ["MALFORMED_ROW_FIELD_COUNT"]))
            continue
        fields: list[Field] = []
        for col_idx, value in enumerate(raw_row):
            # An ambiguous mapping (two distinct headers claiming the same
            # canonical field) KEEPS its role. Which column is "the" UHID is
            # genuinely unknowable and is reported via file_reasons
            # (AMBIGUOUS_HEADER_MAPPING) for identity resolution to weigh --
            # but the ROLE is not ambiguous: two columns both naming a patient
            # identifier are both patient identifiers. Demoting them to OTHER
            # resolved the ambiguity in the one direction that is never safe,
            # because OTHER means RETAIN. Measured on the practice data, one
            # alias that made `sample_id` and `uhid` collide in four lab CSVs
            # published 230 real MRNs in clear and collapsed those rows into
            # per-row pseudonym clusters.
            canonical, role = resolved_cols[col_idx]
            src = SourceCoordinate(
                file_no=file_no,
                file_path=rel_path,
                record_no=record_no,
                path=header[col_idx] or f"col{col_idx}",
            )
            fields.append(Field(
                source=src,
                value_type=ValueType.STRING,
                value=value,
                original_text=value,
                role=role,
            ))
        rows.append(fields)

    return CsvResult(
        file_record=FileRecord(
            file_no, rel_path, declared_format, Disposition.PROCESSED, len(rows),
            reason_codes=file_reasons,
        ),
        rows=rows,
        held_rows=held_rows,
    )
