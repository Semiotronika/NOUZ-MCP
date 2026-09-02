import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from nouz_mcp._version import __version__


REPO_ROOT = Path(__file__).parent


def _stdio_server(vault: Path, *, cache_write: bool = False) -> StdioServerParameters:
    env = os.environ.copy()
    env.update(
        {
            "OBSIDIAN_ROOT": str(vault),
            "NOUZ_READ_ONLY": "true",
            "NOUZ_CACHE_WRITE": "true" if cache_write else "false",
            "EMBED_ENABLED": "false",
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "nouz_mcp.server"],
        cwd=REPO_ROOT,
        env=env,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_protocol"),
    [("auto", "2026-07-28"), ("legacy", "2025-11-25")],
)
async def test_stdio_supports_modern_discover_and_legacy_initialize(
    tmp_path: Path,
    mode: str,
    expected_protocol: str,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sample.md").write_text(
        "---\ntype: quant\nlevel: 4\nsign: S\n---\nSample body\n",
        encoding="utf-8",
    )

    async with Client(_stdio_server(vault), mode=mode) as client:
        assert client.protocol_version == expected_protocol
        assert client.server_info is not None
        assert client.server_info.name == "nouz"
        assert client.server_info.version == __version__
        assert client.instructions

        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        assert names == [
            "read_file",
            "list_files",
            "get_children",
            "get_parents",
            "suggest_metadata",
        ]
        assert all(tool.input_schema.get("type") == "object" for tool in listed.tools)
        assert all(tool.annotations is not None for tool in listed.tools)

        result = await client.call_tool("read_file", {"path": "sample.md"})
        assert result.is_error is False
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["content"] == "Sample body\n"
        assert json.loads(result.content[0].text) == result.structured_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expects_structured_content"),
    [("auto", True), ("legacy", False)],
)
async def test_list_results_are_compatible_with_both_mcp_wire_versions(
    tmp_path: Path,
    mode: str,
    expects_structured_content: bool,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sample.md").write_text("# Sample\n", encoding="utf-8")

    async with Client(_stdio_server(vault, cache_write=True), mode=mode) as client:
        result = await client.call_tool("list_files", {})
        assert result.is_error is False
        text_payload = json.loads(result.content[0].text)
        assert isinstance(text_payload, list)
        if expects_structured_content:
            assert result.structured_content == text_payload
        else:
            assert result.structured_content is None


@pytest.mark.asyncio
async def test_stdio_validates_tool_input_and_returns_model_visible_errors(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    async with Client(_stdio_server(vault), mode="auto") as client:
        invalid = await client.call_tool("read_file", {})
        assert invalid.is_error is True
        assert invalid.structured_content["error"] == "invalid_tool_arguments"
        assert "path" in invalid.structured_content["message"]

        unknown = await client.call_tool("not_a_nouz_tool", {})
        assert unknown.is_error is True
        assert unknown.structured_content == {
            "error": "unknown_tool",
            "message": "Unknown tool: not_a_nouz_tool",
        }


def test_package_requires_mcp_v2_runtime():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert '"mcp>=2,<3"' in pyproject
    assert "mcp>=2,<3" in requirements
    assert '"jsonschema>=4.20"' in pyproject
    assert "jsonschema>=4.20" in requirements
