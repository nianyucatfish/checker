"""Centralized filesystem paths for sidecar runtime data.

Electron can redirect writable locations in packaged builds via CHECKER_*_DIR.
Without overrides, paths intentionally keep the repository-local development layout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_NAME = "AudioQC"


def repo_root() -> Path:
    """Repository root used by the unpackaged development layout."""
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Root for bundled read-only resources; repository root in development."""
    override = os.environ.get("CHECKER_RESOURCE_ROOT")
    return Path(override) if override else repo_root()


def _override(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def data_dir() -> Path:
    """Durable sidecar data directory (state trees)."""
    return _override("CHECKER_DATA_DIR") or repo_root() / "cache"


def cache_dir() -> Path:
    """Regenerable sidecar cache directory."""
    return _override("CHECKER_CACHE_DIR") or repo_root() / "cache"


def log_dir(*, legacy_subdir: str = "cache") -> Path:
    """Log directory, with a caller-selected legacy development location."""
    return _override("CHECKER_LOG_DIR") or repo_root() / legacy_subdir


def config_override() -> Path | None:
    """Exact config path injected by Electron, if configured."""
    return _override("CHECKER_CONFIG")


def app_config_dir() -> Path:
    """Platform user configuration directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_DIR_NAME


def config_candidates() -> list[Path]:
    """Config lookup order: explicit override, development file, platform fallback."""
    candidates: list[Path] = []
    override = config_override()
    if override is not None:
        candidates.append(override)
    candidates.append(repo_root() / "config.toml")
    candidates.append(app_config_dir() / "config.toml")
    return candidates


def default_config_write_path() -> Path:
    """Config destination when no existing config was loaded."""
    return config_override() or app_config_dir() / "config.toml"


def state_tree_dir() -> Path:
    return data_dir() / "state_tree"


def review_log_path() -> Path:
    return log_dir(legacy_subdir="cache") / "review_log.jsonl"


def sheet_cache_path() -> Path:
    return cache_dir() / "sheet_cache.json"


def agent_upstream_log_path() -> Path:
    return log_dir(legacy_subdir="tmp") / "agent_upstream.jsonl"


def midi_debug_dir() -> Path:
    return log_dir(legacy_subdir="tmp")
