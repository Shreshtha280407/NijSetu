"""Secret Wards (Project RAKSHA) organizer adapter.

Translates NijSetu's own internal release contract into the official
`raksha-report/1` submission contract described in the organizer toolkit's
`docs/SPEC.md`:

    {output}/
        sanitized/      a safe-path mirror of the input tree
        report.json      schema "raksha-report/1"
        records.jsonl    one line per exported patient-bearing record

This module owns every organizer-specific shaping decision (output paths,
report schema, locators, pseudonym-in-record consistency). It never
re-implements detection, identity resolution or privacy transformation --
every byte it publishes is a byte `phase2.sanitize.run_sanitize` already
produced and independently validated. It calls that pipeline exactly once;
no identity/clustering decision is ever re-derived here, which is what
keeps `records.jsonl` consistent with the actually-published release by
construction rather than by a second, possibly-diverging pass.
"""

from __future__ import annotations

import json
import os
import re
import resource
import shutil
import tempfile
import time
from pathlib import Path

from collections import Counter

from .config import AppConfig
from .detect import detect_in_text
from .detect.identifiers import (
    find_aadhaar_like,
    find_abha_address,
    find_abha_number,
    find_email,
    find_phone,
    find_pin_code,
    find_unlabelled_local_id,
)
from .ingest.registry import discover_files
from .phase2 import keys
from .phase2.policy import load_policy
from .phase2.sanitize import run_sanitize
from .record_locator import (
    VerificationResult,
    find_duplicate_locators,
    verify_csv_claim,
    verify_document_claim,
    verify_json_claim,
)
from .schema import FieldRole, SourceCoordinate
from .translit.normalize import vowel_collapsed

# Fixed, never derived from the clock/output path/PID: the organizer runs the
# same submission up to three times (same secret twice, a different secret
# once) and compares output trees. Only RAKSHA_SECRET may change what the
# release looks like; release_id must not.
RELEASE_ID = "SECRET-WARDS-RAKSHA-V1"

_PII_PATH_ROLES = frozenset({
    FieldRole.PATIENT_NAME, FieldRole.RELATIVE_NAME, FieldRole.PATIENT_IDENTIFIER,
    FieldRole.CONTACT, FieldRole.FINE_ADDRESS,
})
_FORMAT_EXT = {"csv": "csv", "tsv": "tsv", "json": "json", "txt": "txt", "fhir": "json"}

# Best-effort action-rule -> organizer entity-type label, for report.json's
# optional "entities" breakdown. Never fabricated: only rules sanitize.py
# actually emitted are counted, and an unrecognized rule contributes to no
# label rather than a guessed one.
_RULE_ENTITY_LABEL = {
    "MASK_PATIENT_NAME": "PATIENT_NAME", "FHIR_NAME_SUPPRESSED_PATIENT_NAME": "PATIENT_NAME",
    "MASK_RELATIVE_NAME": "RELATIVE_NAME", "FHIR_NAME_SUPPRESSED_RELATIVE_NAME": "RELATIVE_NAME",
    "MASK_PROVIDER_NAME": "PROVIDER_NAME", "SUPPRESS_PROVIDER_NAME": "PROVIDER_NAME",
    "MASK_FACILITY_NAME": "FACILITY_NAME", "SUPPRESS_FACILITY_NAME": "FACILITY_NAME",
    "IDENTIFIER_TOKENIZED": "PATIENT_IDENTIFIER", "NATIONAL_ID_MASKED": "PATIENT_IDENTIFIER",
    "MASK_PATIENT_IDENTIFIER": "PATIENT_IDENTIFIER",
    "SUPPRESS_CONTACT": "CONTACT", "MASK_CONTACT": "CONTACT", "FHIR_TELECOM_SUPPRESSED": "CONTACT",
    "SUPPRESS_FINE_ADDRESS": "FINE_ADDRESS", "MASK_FINE_ADDRESS": "FINE_ADDRESS",
    "FHIR_FINE_ADDRESS_SUPPRESSED": "FINE_ADDRESS",
    "FHIR_PIN_SUPPRESSED": "PIN",
    "DOB_TO_AGE_BAND": "DOB", "FHIR_BIRTHDATE_SUPPRESSED": "DOB",
}


class OrganizerError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


_FILENAME_TOKEN = re.compile(r"[A-Za-z]+")
# A token recurring across many RECORD-TYPE directories (registration/,
# discharge/, lab/, prescriptions/, billing/, opd/...) and many files is a
# strong, purely structural signal that it names the issuing SOURCE SYSTEM
# (a hospital's own filename convention), not the record type -- a record
# type word naturally clusters inside the one directory that already
# encodes it. These thresholds are NOT tuned to any specific hospital name
# (verified to cleanly separate the four real hospital tokens, all with
# >=4 directories and >=5 files, from incidental shared words like "ward"/
# "export", both capped at 3 directories, on the practice dataset) -- they
# generalize to unseen hospital names because they measure recurrence
# shape, never literal strings.
_SOURCE_TOKEN_MIN_DIRS = 4
_SOURCE_TOKEN_MIN_FILES = 4


def _infer_source_system_map(discovered: list) -> tuple[tuple[str, str], ...]:
    dirs_by_token: dict[str, set[str]] = {}
    files_by_token: dict[str, int] = {}
    tokens_per_file: dict[str, set[str]] = {}
    for df in discovered:
        parts = Path(df.rel_path).parts
        topdir = parts[0] if len(parts) > 1 else ""
        stem = Path(df.rel_path).name.rsplit(".", 1)[0]
        toks = {t.lower() for t in _FILENAME_TOKEN.findall(stem) if len(t) >= 3}
        tokens_per_file[df.rel_path] = toks
        for t in toks:
            dirs_by_token.setdefault(t, set()).add(topdir)
            files_by_token[t] = files_by_token.get(t, 0) + 1

    system_tokens = {
        t for t in dirs_by_token
        if len(dirs_by_token[t]) >= _SOURCE_TOKEN_MIN_DIRS and files_by_token[t] >= _SOURCE_TOKEN_MIN_FILES
    }
    if not system_tokens:
        return ()

    mapping: list[tuple[str, str]] = []
    for df in discovered:
        matched = tokens_per_file[df.rel_path] & system_tokens
        if len(matched) == 1:  # ambiguous (0 or >1 matches) files are left unconfigured, the existing safe default
            mapping.append((df.rel_path, f"SRC:{next(iter(matched))}"))
    return tuple(mapping)


def derive_master_key(raw_secret: str | None) -> bytes:
    if not raw_secret:
        raise OrganizerError("RAKSHA_SECRET_MISSING_OR_EMPTY")
    return keys.derive_master_key_from_secret(raw_secret)


_SEPARATORS = re.compile(r"[_\-]+")


def _normalize_for_match(text: str) -> str:
    """Filenames join semantically separate tokens with `_`/`-`
    ("Discharge_Summary_Reena_Nishad_JJH-0076928"), but `_` is a \\w
    character: a context-free regex boundary (`\\b[6-9]\\d{9}\\b`) that
    works fine on prose fails to find "7585697553" immediately followed by
    "_report" in a filename, because there is no boundary between two \\w
    characters. Replacing every run of `_`/`-` with a single space before
    any detector sees the text (both the candidate filename AND the
    previously-detected literal it is compared against) restores normal
    word boundaries and lets the SAME detectors used on free-text content
    recognize the identical value when it is spelled with underscores."""
    return _SEPARATORS.sub(" ", text).strip()


def _text_has_pii_pattern(text: str) -> bool:
    return bool(
        find_aadhaar_like(text) or find_abha_number(text) or find_abha_address(text)
        or find_phone(text) or find_email(text) or find_pin_code(text)
    )


def _path_unsafe(rel_path: str, file_literals: list[str]) -> bool:
    """A candidate output path is unsafe if (a) any PII literal actually
    detected inside THIS file's own content reappears in its path -- the
    exact same leak the content-side literal scan already guards against,
    just applied to the path instead of the bytes -- or (b) a generic
    identifier/contact pattern matches anywhere in the normalized path,
    independent of whether that specific value happened to also appear in
    the file's body (a phone number used only as a filename, never in the
    content, must still disqualify the path)."""
    normalized_path = _normalize_for_match(rel_path).casefold()
    collapsed_path = vowel_collapsed(normalized_path)
    for literal in file_literals:
        normalized_literal = _normalize_for_match(literal).casefold()
        if normalized_literal and len(normalized_literal) >= 3 and normalized_literal in normalized_path:
            return True
        # Cross-script: a Latin filename ("Reena_Nishad") and a Devanagari
        # content literal ("रीना निषाद") are the same name spelled in two
        # scripts -- vowel_collapsed() is the exact primitive Phase 1's own
        # identity resolution already uses for this (transliterate, strip
        # vowels/non-alnum, keep token spacing), so a genuine same-name
        # pair collapses to an identical substring regardless of script.
        # This is a trigger for the conservative action (rename), not an
        # identity claim, so an approximate collision is an acceptable cost.
        collapsed_literal = vowel_collapsed(literal)
        if len(collapsed_literal) >= 4 and collapsed_literal in collapsed_path:
            return True
    for part in Path(rel_path).parts:
        stem = part.rsplit(".", 1)[0] if "." in part else part
        normalized_stem = _normalize_for_match(stem)
        if not normalized_stem:
            continue
        base = SourceCoordinate(file_no=0, file_path="", record_no=-1, path="filename")
        if any(f.role in _PII_PATH_ROLES for f in detect_in_text(normalized_stem, base)):
            return True
        if _text_has_pii_pattern(normalized_stem):
            return True
        # The label-free identifier route is run on the RAW stem as well,
        # because `_normalize_for_match` flattens `-`/`_` to spaces and those
        # separators are part of the identifier: "jeevanjyoti_ds_IP-26-00130"
        # normalizes to "jeevanjyoti ds IP 26 00130", where no contiguous
        # identifier token survives to be recognised. Separator flattening is
        # right for names and phone numbers and wrong for this one detector,
        # so this detector gets the unflattened text.
        if find_unlabelled_local_id(stem, ignore_case=True):
            return True
    return False


def _safe_relpath(rel_path: str, file_no: int, ext: str, taken: set[str], file_literals: list[str]) -> tuple[str, bool]:
    """Preserve the original relative path (SPEC.md: "keep each file at the
    same relative path") unless the path is unsafe, in which case the WHOLE
    path is replaced by a synthetic, file_no-based name (declared in
    report.json's output mapping, never left to be inferred). Collisions
    (two distinct inputs mapping to the same safe path) are disambiguated
    deterministically."""
    original = Path(rel_path)
    unsafe = _path_unsafe(rel_path, file_literals)
    if not unsafe:
        candidate = original.as_posix()
    else:
        candidate = f"_renamed/file_{file_no}.{ext}"
    if candidate in taken:
        stem, _, suffix = candidate.rpartition(".")
        candidate = f"{stem or candidate}__{file_no}.{suffix}" if suffix else f"{candidate}__{file_no}"
    taken.add(candidate)
    return candidate, unsafe


def _file_level_hold_reason(hold_index: list[dict], file_no: int) -> str | None:
    for entry in hold_index:
        if entry.get("file_no") == file_no and entry.get("record_no") is None:
            codes = entry.get("reason_codes") or []
            return codes[0] if codes else "QUARANTINED"
    return None


def _peak_rss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(ru / 1024, 1) if os.uname().sysname != "Darwin" else round(ru / (1024 * 1024), 1)


def build_output(
    input_dir: Path,
    output_dir: Path,
    raw_secret: str | None,
    policy_name: str = "balanced",
    policy_config: Path | None = None,
) -> int:
    """Returns the process exit code. Writes the complete organizer-contract
    tree into `output_dir` (which must already exist, per the organizer's
    own contract, as an empty directory) as its last step -- everything up
    to that point happens in a private temporary directory so a mid-run
    failure never leaves a partial, falsely-successful-looking release
    behind.

    `policy_name`/`policy_config` exist for the policy trade-off measurement in
    scripts/policy_tradeoff.py and is never set by the submission entrypoint:
    a privacy policy whose cost has not been measured is a default, not a
    choice, and measuring it requires running the SAME code path the
    submission runs -- the internal `nijsetu sanitize` CLI blocks a whole
    release on a residual leak where this path quarantines the one file, so
    comparing variants there would compare a different system. Omitted (the
    only way main.py calls this) the shipped preset is used unchanged.
    """
    wall_start = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise OrganizerError("OUTPUT_DIR_NOT_EMPTY")

    master_key = derive_master_key(raw_secret)
    policy = load_policy(policy_name, policy_config)
    discovered = discover_files(input_dir)
    config = AppConfig(source_system_map=_infer_source_system_map(discovered))

    with tempfile.TemporaryDirectory(prefix="nijsetu-organizer-") as tmp:
        internal_out = Path(tmp) / "internal"
        outcome = run_sanitize(input_dir, internal_out, policy, RELEASE_ID, master_key, config, organizer_contract=True)

        hold_index: list[dict] = []
        if outcome.output_dir is not None:
            hold_path = outcome.output_dir / "hold_index.json"
            if hold_path.is_file():
                hold_index = json.loads(hold_path.read_text(encoding="utf-8"))

        san_out = output_dir / "sanitized"
        san_out.mkdir(parents=True, exist_ok=True)

        files_report: list[dict] = []
        taken_paths: set[str] = set()
        path_map: dict[int, str] = {}
        files_sanitized = 0
        for df in discovered:
            sha256 = _sha256_file(df.abs_path)
            if outcome.output_dir is None:
                files_report.append({"input_sha256": sha256, "output": None, "status": "quarantined",
                                      "reason": "RUN_BLOCKED"})
                continue
            ext = _FORMAT_EXT.get(df.declared_format, "")
            internal_released = outcome.output_dir / "released" / f"file_{df.file_no}.{ext}"
            hold_reason = _file_level_hold_reason(hold_index, df.file_no)
            if hold_reason is not None or not internal_released.is_file():
                files_report.append({
                    "input_sha256": sha256, "output": None, "status": "quarantined",
                    "reason": hold_reason or "UNPROCESSED",
                })
                continue
            safe_rel, _renamed = _safe_relpath(
                df.rel_path, df.file_no, ext, taken_paths, (outcome.file_literals or {}).get(df.file_no, []),
            )
            dest = san_out / safe_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(internal_released, dest)
            path_map[df.file_no] = safe_rel
            files_report.append({"input_sha256": sha256, "output": f"sanitized/{safe_rel}", "status": "sanitized"})
            files_sanitized += 1

        records_lines, claim_rejections = _build_records_jsonl(outcome.records_index or [], path_map, san_out)
        (output_dir / "records.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records_lines), encoding="utf-8",
        )

        inner_report = outcome.report or {}
        inner_counts = inner_report.get("counts", {})
        actions_by_kind = inner_report.get("actions", {}).get("by_kind", {})
        actions_by_rule = inner_report.get("actions", {}).get("by_rule", {})
        entities: dict[str, int] = {}
        for rule, count in actions_by_rule.items():
            label = _RULE_ENTITY_LABEL.get(rule)
            if label:
                entities[label] = entities.get(label, 0) + count

        records_linked = sum(1 for r in records_lines if r.get("patient") is not None)
        # Records that never reached the policy engine because their FILE was
        # quarantined. Previously reported as 0, which made the record
        # accounting unreconcilable: `records_processed` counted only released
        # plus held records, so the records inside an unsupported-encoding or
        # residual-leak file vanished from every total. A reader could not tell
        # whether they had been published, suppressed, or silently dropped.
        #
        # Counted from the ingest layer's own per-file record tally rather than
        # re-derived here, so the number is the one the pipeline actually saw.
        quarantined_file_nos = {
            h.get("file_no") for h in hold_index
            if h.get("disposition") != "HELD" and h.get("file_no") is not None
        }
        records_in_quarantined_files = sum(
            inner_counts.get("records_per_file", {}).get(str(no), 0) for no in quarantined_file_nos
        )
        report = {
            "schema": "raksha-report/1",
            "files": files_report,
            "counts": {
                "files_total": len(discovered),
                "files_sanitized": files_sanitized,
                "files_quarantined": len(discovered) - files_sanitized,
                # Every record the pipeline saw, whatever became of it:
                # released + suppressed + lost to a quarantined file. This is
                # the denominator the other record counts divide into, and
                # tests/test_report_accounting.py reconstructs it independently.
                "records_processed": (
                    inner_counts.get("records_released", 0)
                    + inner_counts.get("records_held", 0)
                    + records_in_quarantined_files
                ),
                "records_released": inner_counts.get("records_released", 0),
                "identifiers_detected": actions_by_kind.get("MASK_SPAN", 0) + actions_by_kind.get("TOKENIZE", 0),
                "records_linked": records_linked,
                "records_generalized": actions_by_kind.get("GENERALIZE", 0),
                "records_suppressed": inner_counts.get("records_held", 0),
                "records_quarantined": records_in_quarantined_files,
                # Lines in records.jsonl. NOT equal to `records_released`, and
                # the difference is structural rather than an error: a claim
                # addresses a LOCATION in a published file, and one location
                # can cover several clinical records -- a FHIR Bundle is one
                # document at one `loc` carrying N resources, all of which
                # `records_released` counts individually because the policy
                # engine acted on each. Both numbers are published, and named
                # for what they count, so neither can be read as the other.
                # tests/test_report_accounting.py asserts the identity below
                # from the released bytes:
                #   records_processed
                #     == records_released + records_suppressed + records_quarantined
                "record_claims_published": len(records_lines),
            },
            "entities": entities,
            # Pseudonym claims that could NOT be verified against the exact
            # exported record, by reason. Counts only -- never a locator, a
            # token or any patient content. An empty object means every
            # published claim was independently re-verified from the released
            # bytes; a non-empty one is a disclosed abstention, not a hidden
            # downgrade (see _build_records_jsonl).
            "record_claim_rejections": claim_rejections,
            "timing": {"seconds": round(time.monotonic() - wall_start, 3)},
            "resources": {"peak_rss_mb": _peak_rss_mb()},
        }
        (output_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if outcome.status not in ("RELEASED", "RELEASED_WITH_HOLDS"):
        return 1
    return 0


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_records_jsonl(
    records_index: list[dict], path_map: dict[int, str], san_out: Path,
) -> tuple[list[dict], dict[str, int]]:
    """Resolves each internal claim to the organizer's {file, loc, out, patient}
    shape and verifies the pseudonym against the EXACT exported record.

    Returns (lines, rejection-reason counts).

    The verification is record-specific, not file-wide. A token appearing in
    patient B's row is no evidence about patient A's row, so a whole-file text
    search let one correctly-tokenized record vouch for every other record in
    the same file -- including records whose pseudonym was wrong or belonged to
    someone else. Each claim now carries an output locator (`out`, per
    SPEC.md), which `record_locator` resolves in the re-parsed exported
    document; the token is sought only inside the resolved row/node.

    Every rejection downgrades that one claim to `patient: null` and is
    counted by reason. Abstaining costs nothing under the organizer's scoring
    ("A record you leave unlinked when the evidence is insufficient costs
    nothing"), whereas publishing an unverifiable claim risks a false merge,
    which is scored as the more serious error.
    """
    file_text_cache: dict[int, str | None] = {}
    rejections: Counter[str] = Counter()

    # A single exported location claimed by two records means at least one
    # claim is wrong with no evidence which; both abstain.
    duplicate_locators = find_duplicate_locators([
        (entry["file_no"], entry.get("out_loc", entry["loc"]))
        for entry in records_index
        if entry.get("patient_token") is not None and path_map.get(entry["file_no"]) is not None
    ])

    def _text_for(file_no: int, safe_rel: str) -> str | None:
        if file_no not in file_text_cache:
            try:
                file_text_cache[file_no] = (san_out / safe_rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                file_text_cache[file_no] = None
        return file_text_cache[file_no]

    out: list[dict] = []
    for entry in records_index:
        file_no = entry["file_no"]
        safe_rel = path_map.get(file_no)
        if safe_rel is None:
            continue  # the file itself did not survive (quarantined) -- no claim to make
        loc = entry["loc"]
        out_loc = entry.get("out_loc", loc)
        token = entry.get("patient_token")

        if token is not None:
            text = _text_for(file_no, safe_rel)
            if text is None:
                result = VerificationResult(False, "EXPORT_UNREADABLE")
            elif (file_no, out_loc) in duplicate_locators:
                result = VerificationResult(False, "DUPLICATE_OUTPUT_LOCATOR")
            else:
                result = _verify_claim(safe_rel, text, out_loc, token)
            if not result.verified:
                rejections[result.reason] += 1
                token = None

        line = {"file": f"sanitized/{safe_rel}", "loc": loc, "patient": token}
        # SPEC.md: `out` is published only when it differs from `loc`.
        if out_loc != loc:
            line["out"] = out_loc
        out.append(line)
    return out, dict(rejections)


def _verify_claim(safe_rel: str, text: str, out_loc: str, token: str) -> VerificationResult:
    """Dispatch on the exported file's own format, which is what determines how
    a locator is addressed."""
    suffix = Path(safe_rel).suffix.lower()
    if out_loc == "doc":
        # The record IS the document (a text note, or a single-patient FHIR
        # Bundle), so document scope is the correct scope here.
        return verify_document_claim(text, token)
    if suffix in (".csv", ".tsv"):
        return verify_csv_claim(text, "\t" if suffix == ".tsv" else ",", out_loc, token)
    if suffix == ".json":
        return verify_json_claim(text, out_loc, token)
    return verify_document_claim(text, token)
