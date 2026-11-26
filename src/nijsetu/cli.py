"""CLI entrypoint: `python -m nijsetu`.

Exit codes (internal contract, not a claimed organizer requirement):
  0 - analysis finished with no held or failed units
  2 - analysis finished with explicit holds/failures
  3 - processing could not complete safely
  4 - invocation or configuration error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AppConfig, Limits, ScoringConfig
from .gate.phase1 import run_gate_phase1
from .gate.phase2 import run_gate_phase2
from .phase2 import load_policy, run_sanitize, validate_release
from .phase2.keys import KeyError_, load_master_key
from .phase2.policy import PolicyError
from .plan import run_pipeline, write_plan


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: D102 - argparse override
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(4)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="nijsetu", description="NijSetu Phase 1: ingest, detect, and link.")
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="Analyze an input file or directory into a private plan + safe report.")
    analyze.add_argument("--input", required=True, help="Input file or directory path.")
    analyze.add_argument("--output", required=True, help="New (empty or nonexistent) output directory.")
    analyze.add_argument("--max-json-bytes", type=int, default=None)
    analyze.add_argument("--max-json-depth", type=int, default=None)
    analyze.add_argument("--max-candidate-bucket", type=int, default=None)
    analyze.add_argument("--record-wrapper-key", default=None, help="Configured JSON record-array wrapper key.")
    analyze.add_argument(
        "--source-system", action="append", default=None, metavar="PATH_PREFIX=ISSUER",
        help="Declare an explicit issuer for a file/directory path prefix (relative to --input). "
             "Repeatable. Two paths given the SAME issuer are deliberately treated as one shared "
             "source system for identifier scoping; unconfigured paths default to a conservative "
             "per-file unknown scope and never merge across files.",
    )

    sanitize = sub.add_parser("sanitize", help="Produce a validated sanitized research release.")
    sanitize.add_argument("--input", required=True, help="Input file or directory path (read-only).")
    sanitize.add_argument("--output", required=True, help="New (empty or nonexistent) release directory.")
    sanitize.add_argument("--policy", required=True, choices=["balanced", "strict"], help="Named policy preset.")
    sanitize.add_argument("--config", default=None, help="Optional JSON file overriding individual policy knobs.")
    sanitize.add_argument("--release-id", required=True, help="Release identifier; rotates every token/date-shift when changed.")
    sanitize.add_argument("--key-file", required=True, help="Local file holding the master secret (>=16 bytes). Never printed.")
    sanitize.add_argument("--max-json-bytes", type=int, default=None)
    sanitize.add_argument("--max-json-depth", type=int, default=None)
    sanitize.add_argument("--max-candidate-bucket", type=int, default=None)
    sanitize.add_argument("--record-wrapper-key", default=None)
    sanitize.add_argument(
        "--source-system", action="append", default=None, metavar="PATH_PREFIX=ISSUER",
        help="Same contract as `analyze --source-system`: repeatable, longest-prefix-wins issuer scoping.",
    )

    validate = sub.add_parser("validate", help="Validate a published release using only its own published content.")
    validate.add_argument("--release", required=True, help="Path to a published release directory.")

    gate = sub.add_parser("gate", help="Run acceptance gates against the real implementation.")
    gate_sub = gate.add_subparsers(dest="gate_name")
    phase1 = gate_sub.add_parser("phase1", help="Run Gate 1 (ingest/detect/link) checks.")
    phase1.add_argument("--output", required=True, help="New (empty or nonexistent) evidence directory.")
    phase2 = gate_sub.add_parser("phase2", help="Run Gate 2 (sanitize/validate) checks.")
    phase2.add_argument("--output", required=True, help="New (empty or nonexistent) evidence directory.")

    return parser


class _SourceSystemArgError(Exception):
    pass


def _parse_source_system(entries: list[str] | None) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for entry in entries or []:
        if "=" not in entry:
            raise _SourceSystemArgError(entry)
        prefix, issuer = entry.split("=", 1)
        if not prefix or not issuer:
            raise _SourceSystemArgError(entry)
        out.append((prefix, issuer))
    return tuple(out)


def _config_from_args(args: argparse.Namespace) -> AppConfig:
    limits = Limits()
    if args.max_json_bytes is not None:
        limits.max_json_bytes = args.max_json_bytes
    if args.max_json_depth is not None:
        limits.max_json_depth = args.max_json_depth
    if args.max_candidate_bucket is not None:
        limits.max_candidate_bucket = args.max_candidate_bucket
    return AppConfig(
        limits=limits, scoring=ScoringConfig(), record_wrapper_key=args.record_wrapper_key,
        source_system_map=_parse_source_system(args.source_system),
    )


def _run_analyze(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"status": "ERROR", "reason_code": "INPUT_PATH_NOT_FOUND"}), file=sys.stderr)
        return 4
    output_dir = Path(args.output)
    if output_dir.exists() and any(output_dir.iterdir()):
        print(json.dumps({"status": "ERROR", "reason_code": "OUTPUT_DIR_NOT_EMPTY"}), file=sys.stderr)
        return 4

    try:
        config = _config_from_args(args)
    except _SourceSystemArgError as exc:
        print(json.dumps({"status": "ERROR", "reason_code": "INVALID_SOURCE_SYSTEM_ARG", "detail": str(exc)}), file=sys.stderr)
        return 4

    try:
        plan = run_pipeline(input_path, config)
    except Exception:  # noqa: BLE001 - never leak a traceback: it could carry patient data
        print(json.dumps({"status": "BLOCKED", "reason_code": "UNEXPECTED_PROCESSING_FAILURE"}), file=sys.stderr)
        return 3

    write_plan(plan, output_dir)
    print(json.dumps(plan.safe_report, indent=2))
    return 0 if plan.safe_report["status"] == "CLEAN" else 2


def _run_sanitize(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"status": "ERROR", "reason_code": "INPUT_PATH_NOT_FOUND"}), file=sys.stderr)
        return 4
    output_dir = Path(args.output)

    try:
        config = _config_from_args(args)
    except _SourceSystemArgError as exc:
        print(json.dumps({"status": "ERROR", "reason_code": "INVALID_SOURCE_SYSTEM_ARG", "detail": str(exc)}), file=sys.stderr)
        return 4

    config_path = Path(args.config) if args.config else None
    try:
        policy = load_policy(args.policy, config_path)
    except PolicyError as exc:
        print(json.dumps({"status": "ERROR", "reason_code": exc.reason_code}), file=sys.stderr)
        return 4

    try:
        master_key = load_master_key(Path(args.key_file))
    except KeyError_ as exc:
        print(json.dumps({"status": "ERROR", "reason_code": exc.reason_code}), file=sys.stderr)
        return 4

    if not args.release_id:
        print(json.dumps({"status": "ERROR", "reason_code": "RELEASE_ID_EMPTY"}), file=sys.stderr)
        return 4

    try:
        outcome = run_sanitize(input_path, output_dir, policy, args.release_id, master_key, config)
    except Exception:  # noqa: BLE001 - never leak a traceback: it could carry patient data
        print(json.dumps({"status": "BLOCKED", "reason_code": "UNEXPECTED_PROCESSING_FAILURE"}), file=sys.stderr)
        return 3

    print(json.dumps(outcome.report, indent=2))
    if outcome.status == "ERROR":
        return 4
    if outcome.status == "BLOCKED":
        return 3
    if outcome.status == "RELEASED_WITH_HOLDS":
        return 2
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    release_dir = Path(args.release)
    if not release_dir.is_dir():
        print(json.dumps({"status": "ERROR", "reason_code": "RELEASE_PATH_NOT_FOUND"}), file=sys.stderr)
        return 4
    result = validate_release(release_dir)
    print(json.dumps({"passed": result.passed, "checks": result.checks, "scope_note": result.scope_note}, indent=2))
    return 0 if result.passed else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0

    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "sanitize":
        return _run_sanitize(args)
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "gate":
        if args.gate_name == "phase1":
            return run_gate_phase1(Path(args.output))
        if args.gate_name == "phase2":
            return run_gate_phase2(Path(args.output))
        print(json.dumps({"status": "ERROR", "reason_code": "UNKNOWN_GATE"}), file=sys.stderr)
        return 4

    parser.print_help()
    return 4


if __name__ == "__main__":
    sys.exit(main())
