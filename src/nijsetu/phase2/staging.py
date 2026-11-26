"""Restricted private staging + atomic publication.

Staging lives as a hidden sibling of the final --output directory (same
filesystem, guaranteeing the final promotion is a single atomic rename) but
is never itself inside the publish/download path. `public/` holds exactly
what may become the release; `private/` holds identity maps and raw
evidence and is deleted -- never promoted -- regardless of outcome.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path


class StagingError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _owner_pid_alive(staging_dir: Path) -> bool:
    """True if the process that created this staging dir is still running
    (or its liveness cannot be determined, which is treated as "assume
    alive" -- the safe side for a concurrent job's active directory)."""
    pid_file = staging_dir / ".owner_pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_staging(output_dir: Path) -> None:
    """Best-effort: remove staging litter a previous *interrupted* run left
    behind as a sibling of this --output path. Only ever removes a staging
    directory whose owning process is demonstrably gone -- a directory still
    owned by a live concurrent job (targeting a different --output in the
    same parent) must survive this call. Never touches output_dir itself."""
    parent = output_dir.parent
    if not parent.is_dir():
        return
    for p in parent.glob(".nijsetu_stage_*"):
        if not _owner_pid_alive(p):
            shutil.rmtree(p, ignore_errors=True)


class Staging:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        parent = output_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=".nijsetu_stage_", dir=str(parent)))
        os.chmod(self.root, stat.S_IRWXU)
        (self.root / ".owner_pid").write_text(str(os.getpid()), encoding="utf-8")
        self.public = self.root / "public"
        self.private = self.root / "private"
        self.public.mkdir(parents=True)
        self.private.mkdir(parents=True)
        os.chmod(self.private, stat.S_IRWXU)

    def publish(self) -> None:
        """Atomic promotion: os.rename on the same filesystem. Refuses to
        touch an existing non-empty destination; an existing empty directory
        (e.g. the caller pre-created --output) is removed first so the
        rename lands on a genuinely fresh path."""
        if self.output_dir.exists():
            if any(self.output_dir.iterdir()):
                raise StagingError("OUTPUT_DIR_NOT_EMPTY")
            self.output_dir.rmdir()
        try:
            os.rename(str(self.public), str(self.output_dir))
        except OSError:
            raise StagingError("CONCURRENT_PUBLISH_CONFLICT") from None

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
