"""Public plan API: run the pipeline and persist its two artifacts."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .builder import AnalysisPlan, run_pipeline

__all__ = ["AnalysisPlan", "run_pipeline", "write_plan"]


def write_plan(plan: AnalysisPlan, output_dir: Path) -> tuple[Path, Path]:
    """Writes safe_report (normal perms) and the private artifact (0600, own dir).

    Returns (safe_report_path, private_artifact_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(plan.safe_report, indent=2, ensure_ascii=False), encoding="utf-8")

    private_dir = output_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(private_dir, stat.S_IRWXU)  # owner-only directory
    private_path = private_dir / "analysis.json"
    private_path.write_text(json.dumps(plan.private, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner read/write only

    return report_path, private_path
