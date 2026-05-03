import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OBSIDIAN_ROOT", tempfile.mkdtemp())
os.environ.setdefault("EMBED_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402


def test_repository_wrapper_exposes_server_api():
    assert server.VERSION == "3.0.2"
    assert callable(server.run_server)
    assert callable(server.main)
    assert callable(server._dump_metadata)


def test_frontmatter_parser_reads_yaml_and_body():
    raw = "---\ntype: quant\nlevel: 4\nsign: T\n---\nBody text"
    attrs, body = server._parse_frontmatter(raw)

    assert attrs["type"] == "quant"
    assert attrs["level"] == 4
    assert attrs["sign"] == "T"
    assert body.strip() == "Body text"


def test_metadata_dump_does_not_write_internal_fields():
    dumped = server._dump_metadata({
        "type": "quant",
        "level": 4,
        "sign": "T",
        "content": "hidden",
        "path": "hidden.md",
        "core_mix": {"T": 1.0},
    })

    assert "type: quant" in dumped
    assert "content:" not in dumped
    assert "path:" not in dumped
    assert "core_mix:" not in dumped
