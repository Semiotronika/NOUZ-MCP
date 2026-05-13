"""Path helpers for vault and SQLite cache access."""

import os
from pathlib import Path
from typing import Optional


def default_db_path(obsidian_root: str, database_name: str, database_path: str = "") -> str:
    """Return the active SQLite cache path for this server instance."""
    if database_path:
        return database_path
    return os.path.join(obsidian_root, database_name)


def db_path_to_file(path_str: str, obsidian_root: str) -> Path:
    """Resolve a path stored in SQLite back to a Markdown file path."""
    p = Path(path_str)
    if p.is_absolute() or p.exists():
        return p
    return Path(obsidian_root) / p


def safe_path(root: str, rel: str) -> Optional[Path]:
    """Resolve a user-supplied relative path and reject path traversal."""
    root_path = Path(root).resolve()
    full = (root_path / rel).resolve()
    if root_path in full.parents or full == root_path:
        return full
    return None
