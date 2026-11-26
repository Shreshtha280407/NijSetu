"""Configurable local limits and policy knobs. Internal defaults, not organizer requirements."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Limits:
    max_json_bytes: int = 32 * 1024 * 1024  # 32 MiB per JSON unit
    max_json_depth: int = 64
    max_txt_bytes: int = 16 * 1024 * 1024
    max_candidate_bucket: int = 200  # hard cap on comparisons within one block
    supported_encodings: tuple[str, ...] = ("utf-8", "utf-8-sig", "utf-16")


@dataclass
class ScoringConfig:
    """Thresholds frozen on synthetic development data (see PHASE1_REPORT.md)."""

    corroborated_floor: float = 62.0
    sparse_but_distinct_floor: float = 48.0
    runner_up_margin: float = 8.0
    household_signal_cap: float = 12.0


@dataclass
class AppConfig:
    limits: Limits = field(default_factory=Limits)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    record_wrapper_key: str | None = None  # configurable JSON record-array wrapper
    extra_encodings: tuple[str, ...] = ()  # explicitly declared per-source only
    # Explicit source-system (issuer) mapping: (path_prefix, issuer) pairs.
    # A record's file path is matched against each prefix (exact path, or a
    # directory boundary via "prefix/"); the longest matching prefix wins.
    # Unmatched paths fall back to a conservative per-file unknown scope --
    # see identity.resolve_fallback_issuer (also reused by Phase 2's
    # identifier tokenization, so a local scheme tokenizes under the same
    # issuer scope it was clustered under).
    source_system_map: tuple[tuple[str, str], ...] = ()


def resolve_configured_issuer(file_path: str, source_system_map: tuple[tuple[str, str], ...]) -> str | None:
    """Longest-prefix match of file_path against a configured (prefix, issuer) map."""
    best_prefix = ""
    best_issuer: str | None = None
    for prefix, issuer in source_system_map:
        if file_path == prefix or file_path.startswith(prefix.rstrip("/") + "/"):
            if len(prefix) > len(best_prefix):
                best_prefix, best_issuer = prefix, issuer
    return best_issuer
