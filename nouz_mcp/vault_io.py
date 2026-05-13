"""Vault file IO helpers for NOUZ Markdown files."""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import aiofiles


ParseFrontmatter = Callable[[str], Tuple[Dict[str, Any], str]]
SerializeValue = Callable[[Any], Any]


async def read_file_with_metadata(
    file_path: Path,
    *,
    parse_frontmatter: ParseFrontmatter,
    serialize_value: SerializeValue,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Read a Markdown file and return frontmatter plus body content."""
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as handle:
            raw = await handle.read()
        try:
            attrs, body = parse_frontmatter(raw)
            meta = {key: serialize_value(value) for key, value in attrs.items()}
            meta["content"] = body
        except Exception as exc:
            if logger:
                logger.warning(f"frontmatter parse error for {file_path.name}, using fallback: {exc}")
            meta = {"path": str(file_path), "content": raw, "frontmatter_error": str(exc)}
        meta["path"] = str(file_path)
        return meta
    except Exception as exc:
        if logger:
            logger.error(f"Error reading {file_path}: {exc}")
        return {"path": str(file_path), "content": "", "error": str(exc)}


async def read_text(file_path: Path) -> str:
    """Read one vault file as plain text."""
    async with aiofiles.open(file_path, "r", encoding="utf-8") as handle:
        return await handle.read()


async def write_text(file_path: Path, text: str) -> None:
    """Write one vault file as plain text, creating parent directories."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(file_path, "w", encoding="utf-8") as handle:
        await handle.write(text)
