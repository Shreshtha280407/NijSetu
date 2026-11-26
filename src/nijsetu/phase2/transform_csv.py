"""CSV/TSV sanitizing rewriter: preserves column order, delimiter, quoting and
row shape; rewrites only the cells (and, rarely, headers) actually flagged."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field

from ..detect import detect_in_text
from ..ingest.header_aliases import resolve_header
from ..schema import Action, Field, FieldRole, SourceCoordinate
from .actions import decide_feature_action
from .patient_context import PatientContext
from .policy import PolicyConfig
from .spans import apply_span_replacements, resolve_overlaps

# A header that strips down to a known schema-label alias (e.g. "Patient
# Name", "patientName", "UHID", or a trailing-punctuation variant like
# "Patient Name:") is an ordinary column label, never scanned -- scanning it
# with the same cue patterns used for free text is exactly what causes a
# false positive (Patient/Pt cue + "Name" captured as if it were a name).
# Anything that does NOT resolve to a known label (e.g. "Patient: Qzylom
# Narex", a real value that ended up in header position) gets the full
# free-text scan, same detectors used everywhere else.
_LABEL_TRAILING_PUNCT = ":-–— \t"


def is_known_schema_label(h: str) -> bool:
    stripped = h.strip().rstrip(_LABEL_TRAILING_PUNCT).strip()
    canonical, _role = resolve_header(stripped)
    return canonical is not None


def header_leak_features(h: str):
    """Independent header-leak check, reused by validate's final export scan:
    returns the detected Features (empty if the header is a known label or
    clean free text)."""
    if not h.strip() or is_known_schema_label(h):
        return []
    base = SourceCoordinate(file_no=0, file_path="", record_no=-1, path="header")
    return detect_in_text(h, base)


@dataclass
class CsvTransformResult:
    text: str
    actions: list[Action]
    header_renamed: dict[str, str]
    # Structured sanitized rows (rows[0] is the header) -- the authoritative
    # per-logical-record view. `text` is a convenience serialization of the
    # same rows and must never be re-split by physical line: a quoted
    # multi-line cell's embedded newline would desynchronize a line index
    # from the logical row it belongs to.
    rows: list[list[str]] = field(default_factory=list)
    held: bool = False
    hold_reasons: list[str] | None = None


def _safe_headers(header: list[str]) -> tuple[list[str], dict[str, str], list[Action]]:
    """A header that resolves to a known schema label (e.g. "Patient Name",
    "patientName", "UHID", "BP", "Dose") is an ordinary column label fixed
    once per file -- preserved verbatim, never scanned. Any other header is
    still free text some exporter could have filled with an actual value
    (e.g. a mis-shifted row turning "Patient: Qzylom Narex" into a header),
    so it gets the full free-text scan (contextual name cues, known
    aliases, identifier/date formats) before being trusted. Collisions are
    still checked by the caller."""
    out: list[str] = []
    renamed: dict[str, str] = {}
    actions: list[Action] = []
    for i, h in enumerate(header):
        if header_leak_features(h):
            new_h = f"SANITIZED_HEADER_{i}"
            renamed[h] = new_h
            actions.append(Action(target=f"header[{i}]", kind="TOKENIZE", rule_id="CSV_HEADER_SANITIZED"))
            out.append(new_h)
            continue
        out.append(h)
    return out, renamed, actions


def transform_csv(
    header: list[str],
    rows: list[list[Field]],
    row_features: dict[int, list],
    row_patient_ctx: dict[int, PatientContext],
    policy: PolicyConfig,
    release_key: bytes,
    delimiter: str,
) -> CsvTransformResult:
    out_header, renamed, header_actions = _safe_headers(header)
    if len(set(out_header)) != len(out_header):
        return CsvTransformResult(text="", actions=[], header_renamed=renamed, held=True,
                                   hold_reasons=["CSV_HEADER_COLLISION_AFTER_SANITIZATION"])

    actions: list[Action] = list(header_actions)
    out_rows: list[list[str]] = [out_header]
    for record_no, fields in enumerate(rows):
        feats_by_path: dict[str, list] = defaultdict(list)
        for f in row_features.get(record_no, []):
            feats_by_path[f.source.path].append(f)
        ctx = row_patient_ctx[record_no]
        new_row: list[str] = []
        for fld in fields:
            col_feats = feats_by_path.get(fld.source.path, [])
            whole_field = (
                fld.role != FieldRole.OTHER and len(col_feats) == 1
                and col_feats[0].char_start == 0 and col_feats[0].char_end == len(fld.value)
            )
            target = f"row{record_no}.{fld.source.path}"
            if whole_field:
                result = decide_feature_action(col_feats[0], policy, ctx, release_key, target)
                new_row.append("" if result.omit_field else result.replacement_text)
                actions.append(result.action)
            elif col_feats:
                resolved = resolve_overlaps(col_feats)
                repls = []
                for f in resolved:
                    result = decide_feature_action(f, policy, ctx, release_key, target)
                    repls.append((f.char_start, f.char_end, result.replacement_text))
                    actions.append(result.action)
                new_row.append(apply_span_replacements(fld.value, repls))
            else:
                new_row.append(fld.value)
        out_rows.append(new_row)

    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerows(out_rows)
    return CsvTransformResult(text=buf.getvalue(), actions=actions, header_renamed=renamed, rows=out_rows)
