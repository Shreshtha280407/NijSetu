"""Format registry and dispatch: input path -> typed fields, uniformly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..errors import LimitExceededError, MalformedInputError, UnsupportedFormatError
from ..schema import Field, FileRecord, Disposition
from .csv_adapter import parse_csv
from .fhir_adapter import FhirResource, parse_fhir
from .json_adapter import parse_json
from .registry import DiscoveredFile, discover_files
from .txt_adapter import TxtDocument, parse_txt

UNSUPPORTED_EXTENSIONS = {".xml", ".pdf", ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg"}


@dataclass
class DocumentUnit:
    """One processable unit: a CSV/TSV row, a JSON record, a FHIR resource, or a TXT file."""

    file_no: int
    file_path: str
    format: str
    record_no: int
    fields: list[Field] = field(default_factory=list)
    txt_document: TxtDocument | None = None
    fhir_resource: FhirResource | None = None


@dataclass
class HeldRecord:
    file_no: int
    file_path: str
    record_no: int | None  # None when the file-level count itself is unknown
    reason_codes: list[str]


@dataclass
class IngestResult:
    file_records: list[FileRecord] = field(default_factory=list)
    documents: list[DocumentUnit] = field(default_factory=list)
    held_records: list[HeldRecord] = field(default_factory=list)
    fhir_resources: list[FhirResource] = field(default_factory=list)


def run_ingest(input_path: Path, config: AppConfig) -> IngestResult:
    result = IngestResult()
    files = discover_files(input_path)
    if not files:
        return result

    for df in files:
        _process_file(df, config, result)
    return result


def _process_file(df: DiscoveredFile, config: AppConfig, result: IngestResult) -> None:
    rel = df.rel_path
    fmt = df.declared_format

    if fmt == "unknown":
        reason = "UNSUPPORTED_EXTENSION" if df.abs_path.suffix.lower() in UNSUPPORTED_EXTENSIONS else "UNKNOWN_FORMAT"
        result.file_records.append(FileRecord(df.file_no, rel, "unknown", Disposition.UNSUPPORTED, None, [reason]))
        return

    try:
        raw = df.abs_path.read_bytes()
    except OSError:
        result.file_records.append(FileRecord(df.file_no, rel, fmt, Disposition.PARSE_FAILED, None, ["FILE_READ_ERROR"]))
        return

    try:
        if fmt in ("csv", "tsv"):
            parsed = parse_csv(raw, rel, df.file_no, fmt)
            result.file_records.append(parsed.file_record)
            for record_no, row_fields in enumerate(parsed.rows):
                result.documents.append(DocumentUnit(df.file_no, rel, fmt, record_no, fields=row_fields))
            for record_no, reasons in parsed.held_rows:
                result.held_records.append(HeldRecord(df.file_no, rel, record_no, reasons))
        elif fmt == "json":
            parsed = parse_json(raw, rel, df.file_no, config.limits, config.record_wrapper_key)
            result.file_records.append(parsed.file_record)
            for record_no, rec_fields in enumerate(parsed.records):
                result.documents.append(DocumentUnit(df.file_no, rel, fmt, record_no, fields=rec_fields))
        elif fmt == "txt":
            parsed = parse_txt(raw, rel, df.file_no)
            result.file_records.append(parsed.file_record)
            if parsed.file_record.record_count:
                result.documents.append(DocumentUnit(df.file_no, rel, fmt, 0, txt_document=parsed.document))
        elif fmt == "fhir":
            parsed = parse_fhir(raw, rel, config.limits, file_no=df.file_no)
            result.file_records.append(parsed.file_record)
            result.fhir_resources.extend(parsed.resources)
            for res in parsed.resources:
                result.documents.append(DocumentUnit(df.file_no, rel, fmt, res.resource_no, fields=res.fields, fhir_resource=res))
            for ordinal, reasons in parsed.held_resources:
                result.held_records.append(HeldRecord(df.file_no, rel, ordinal, reasons))
        else:
            result.file_records.append(FileRecord(df.file_no, rel, fmt, Disposition.UNSUPPORTED, None, ["UNSUPPORTED_FORMAT"]))
    except (UnsupportedFormatError, LimitExceededError, MalformedInputError) as exc:
        result.file_records.append(FileRecord(df.file_no, rel, fmt, Disposition.QUARANTINED, None, [exc.reason_code]))
