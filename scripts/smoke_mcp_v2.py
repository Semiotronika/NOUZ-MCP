#!/usr/bin/env python3
"""Read-only MCP v2 smoke against a real Markdown/Obsidian vault."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nouz_mcp._version import __version__  # noqa: E402


EXPECTED_PROTOCOLS = {"auto": "2026-07-28", "legacy": "2025-11-25"}
EXPECTED_LUCA_TOOLS = {
    "read_file",
    "list_files",
    "get_children",
    "get_parents",
    "suggest_metadata",
}


def _vault_state(vault: Path) -> tuple[tuple[str, int, int], ...]:
    """Capture metadata only; never include vault content in the report."""
    entries: list[tuple[str, int, int]] = []
    for path in vault.rglob("*"):
        if path.is_file():
            stat = path.stat()
            entries.append((path.relative_to(vault).as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(entries))


def _stdio_server(repo_root: Path, vault: Path, database_path: Path):
    env = os.environ.copy()
    env.update(
        {
            "OBSIDIAN_ROOT": str(vault),
            "NOUZ_READ_ONLY": "true",
            "NOUZ_CACHE_WRITE": "true",
            "NOUZ_CONFIG": "",
            "NOUZ_DATABASE_PATH": str(database_path),
            "EMBED_ENABLED": "false",
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["server.py"],
        cwd=repo_root,
        env=env,
    )
    return stdio_client(params)


def _json_payload(result: Any) -> Any:
    if result.is_error:
        raise RuntimeError("NOUZ returned an MCP tool error")
    if not result.content or result.content[0].type != "text":
        raise RuntimeError("NOUZ returned no textual tool payload")
    return json.loads(result.content[0].text)


async def _run_mode(repo_root: Path, vault: Path, database_path: Path, mode: str) -> dict[str, Any]:
    async with Client(_stdio_server(repo_root, vault, database_path), mode=mode) as client:
        expected_protocol = EXPECTED_PROTOCOLS[mode]
        if client.protocol_version != expected_protocol:
            raise RuntimeError(
                f"{mode} negotiated {client.protocol_version!r}, expected {expected_protocol!r}"
            )
        if client.server_info is None:
            raise RuntimeError("NOUZ did not return server information")
        if client.server_info.name != "nouz" or client.server_info.version != __version__:
            raise RuntimeError("NOUZ server identity/version contract failed")

        listed = await client.list_tools()
        tool_names = {tool.name for tool in listed.tools}
        if tool_names != EXPECTED_LUCA_TOOLS:
            raise RuntimeError(f"unexpected LUCA tool set: {sorted(tool_names)!r}")

        listed_result = await client.call_tool("list_files", {})
        files = _json_payload(listed_result)
        if not isinstance(files, list) or not files:
            raise RuntimeError("real vault returned no Markdown files")
        sample_path = next(
            (item.get("path") for item in files if isinstance(item, dict) and isinstance(item.get("path"), str)),
            None,
        )
        if not sample_path:
            raise RuntimeError("real vault file listing had no usable path")

        read_result = await client.call_tool("read_file", {"path": sample_path})
        note = _json_payload(read_result)
        if not isinstance(note, dict) or not isinstance(note.get("content"), str):
            raise RuntimeError("real vault read_file returned no Markdown content")

        return {
            "mode": mode,
            "protocol_version": client.protocol_version,
            "server_version": client.server_info.version,
            "tool_count": len(listed.tools),
            "markdown_count": len(files),
            "sample_note_read": True,
            "sample_content_chars": len(note["content"]),
        }


async def _main(vault: Path, database_path: Path, modes: list[str]) -> None:
    before = _vault_state(vault)
    summaries = []
    for mode in modes:
        summaries.append(await _run_mode(REPO_ROOT, vault, database_path, mode))
    after = _vault_state(vault)
    if before != after:
        raise RuntimeError("real vault metadata changed during read-only smoke")
    print(json.dumps({"vault_unchanged": True, "results": summaries}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, help="real Markdown/Obsidian vault to inspect")
    parser.add_argument(
        "--database-path",
        type=Path,
        default=REPO_ROOT / ".test-tmp" / "real-vault-mcp-v2.sqlite3",
        help="isolated SQLite cache path; must be outside the vault",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "legacy", "both"],
        default="both",
        help="protocol mode to exercise",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    vault = args.vault.expanduser().resolve()
    database_path = args.database_path.expanduser().resolve()
    if not vault.is_dir():
        raise SystemExit(f"vault is not a directory: {vault}")
    if database_path.parent == vault or vault in database_path.parents:
        raise SystemExit("database path must be outside the vault")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    modes = ["auto", "legacy"] if args.mode == "both" else [args.mode]
    asyncio.run(_main(vault, database_path, modes))


if __name__ == "__main__":
    main()
