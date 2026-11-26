#!/usr/bin/env python3
"""Secret Wards (Project RAKSHA, Track 3) submission entrypoint.

    python main.py --input {input} --output {output}

The organizer's evaluator substitutes real absolute paths for {input}/
{output} and supplies the per-release secret as the RAKSHA_SECRET
environment variable (see raksha.toml at the repository root for the exact
setup/run commands). This file is intentionally a thin shim: all real
logic lives in the maintained `nijsetu.organizer` module, so the existing
internal `python -m nijsetu ...` CLI (analyze/sanitize/validate/gate) keeps
working completely unchanged -- see README.md for the full architecture.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

try:
    from nijsetu.organizer import OrganizerError, build_output
except ImportError:
    # Fall back to the source checkout directly if the package was not
    # actually installed into the active interpreter (it always should be,
    # per raksha.toml's `setup`) -- never required for an installed run.
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    from nijsetu.organizer import OrganizerError, build_output  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secret Wards submission entrypoint")
    parser.add_argument("--input", required=True, help="Read-only input directory.")
    parser.add_argument("--output", required=True, help="Pre-created, empty output directory.")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.exists():
        print("input path not found", file=sys.stderr)
        return 4

    try:
        return build_output(input_dir, output_dir, os.environ.get("RAKSHA_SECRET"))
    except OrganizerError as exc:
        # Safe by construction: a reason code only -- never patient data,
        # never RAKSHA_SECRET, never a raw traceback.
        print(f"organizer adapter error: {exc.reason_code}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - a traceback could quote patient content; never print one
        print("organizer adapter error: UNEXPECTED_FAILURE", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
