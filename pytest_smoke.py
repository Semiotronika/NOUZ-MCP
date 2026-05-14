import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import aiosqlite

os.environ.setdefault("OBSIDIAN_ROOT", tempfile.mkdtemp())
os.environ.setdefault("EMBED_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).parent))

from nouz_mcp import server  # noqa: E402
from nouz_mcp._version import __version__  # noqa: E402
from nouz_mcp import calc_etalons  # noqa: E402
from nouz_mcp.chunks import chunk_markdown  # noqa: E402
from nouz_mcp.links import check_parents_exist, get_parents_meta  # noqa: E402
from nouz_mcp.markdown import canonical_tag, dump_metadata, explicit_tag_list, explicit_tag_report, parse_frontmatter, split_frontmatter_raw, sync_parents_fields  # noqa: E402
from nouz_mcp.modes import build_rules, get_level, get_type_by_level  # noqa: E402
from nouz_mcp.paths import default_db_path, safe_path  # noqa: E402
from nouz_mcp.serialization import serialize  # noqa: E402
from nouz_mcp.semantics import get_embedding  # noqa: E402
from nouz_mcp.signs import (  # noqa: E402
    artifact_keywords,
    artifact_sign,
    dedupe_sign_chars,
    determine_artifact_sign,
    extract_artifact_sign_from_sign,
    extract_core_sign_from_sign,
    signs_share_core,
)
from nouz_mcp.sqlite_store import (  # noqa: E402
    aggregate_core_mix as store_aggregate_core_mix,
    check_cycle_exists as store_check_cycle_exists,
    chunk_embeddings_are_fresh as store_chunk_embeddings_are_fresh,
    embedding_is_fresh as store_embedding_is_fresh,
    find_entity_path_by_stem as store_find_entity_path_by_stem,
    find_orphaned_links as store_find_orphaned_links,
    get_core_mix as store_get_core_mix,
    get_db_children as store_get_db_children,
    get_db_parents as store_get_db_parents,
    get_file_summaries as store_get_file_summaries,
    index_file as store_index_file,
    list_chunk_embeddings as store_list_chunk_embeddings,
    list_core_anchor_candidates as store_list_core_anchor_candidates,
    init_db as init_sqlite_store,
    list_embedding_candidates as store_list_embedding_candidates,
    list_hierarchy_child_paths as store_list_hierarchy_child_paths,
    list_parent_embedding_candidates as store_list_parent_embedding_candidates,
    list_semantic_bridge_rows as store_list_semantic_bridge_rows,
    list_sign_recalc_rows as store_list_sign_recalc_rows,
    list_tag_bridge_rows as store_list_tag_bridge_rows,
    load_reference_vectors as store_load_reference_vectors,
    load_embedding as store_load_embedding,
    save_chunk_embeddings as store_save_chunk_embeddings,
    save_embedding as store_save_embedding,
    save_reference_vector as store_save_reference_vector,
    update_sign_recalc_rows as store_update_sign_recalc_rows,
)
from nouz_mcp.use_cases import add_entity as add_entity_use_case  # noqa: E402
from nouz_mcp.use_cases import index_all_files as index_all_files_use_case  # noqa: E402
from nouz_mcp.use_cases import list_files as list_files_use_case  # noqa: E402
from nouz_mcp.use_cases import process_orphans as process_orphans_use_case  # noqa: E402
from nouz_mcp.use_cases import read_file as read_file_use_case  # noqa: E402
from nouz_mcp.use_cases import recalc_core_mix as recalc_core_mix_use_case  # noqa: E402
from nouz_mcp.use_cases import recalc_signs as recalc_signs_use_case  # noqa: E402
from nouz_mcp.use_cases import search_chunk_embeddings as search_chunk_embeddings_use_case  # noqa: E402
from nouz_mcp.use_cases import suggest_tag_candidates as suggest_tag_candidates_use_case  # noqa: E402
from nouz_mcp.use_cases import suggest_tag_bridges as suggest_tag_bridges_use_case  # noqa: E402
from nouz_mcp.use_cases import suggest_metadata as suggest_metadata_use_case  # noqa: E402
from nouz_mcp.use_cases import suggest_parents as suggest_parents_use_case  # noqa: E402
from nouz_mcp.use_cases import update_metadata as update_metadata_use_case  # noqa: E402
from nouz_mcp.use_cases import write_file as write_file_use_case  # noqa: E402
from nouz_mcp.use_cases import write_file_with_metadata as write_file_with_metadata_use_case  # noqa: E402
from nouz_mcp.vault_io import read_file_with_metadata as read_vault_file_with_metadata  # noqa: E402
from nouz_mcp.vault_io import read_text as read_vault_text  # noqa: E402
from nouz_mcp.vault_io import write_text as write_vault_text  # noqa: E402
from nouz_mcp.vectors import cosine, mean_center  # noqa: E402


def test_package_server_exposes_server_api():
    assert __version__ == "3.1.0"
    assert server.VERSION == __version__
    assert callable(server.run_server)
    assert callable(server.main)


def test_read_only_tool_filter_hides_mutating_tools():
    tools = [
        server.types.Tool(name="read_file", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="write_file", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="index_all", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="suggest_metadata", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="recalc_signs", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="chunk_file", description="", inputSchema={"type": "object"}),
        server.types.Tool(name="search_chunks", description="", inputSchema={"type": "object"}),
    ]

    visible = server.filter_read_only_tools(tools, read_only=True)
    visible_names = {tool.name for tool in visible}

    assert visible_names == {"read_file", "suggest_metadata", "chunk_file", "search_chunks"}
    assert server.filter_read_only_tools(tools, read_only=False) == tools
    assert server.is_read_only_disabled_tool("write_file") is True
    assert server.is_read_only_disabled_tool("chunk_file") is False
    assert server.is_read_only_disabled_tool("search_chunks") is False


def test_read_only_env_disables_cache_writes_by_default():
    script = (
        "import os, tempfile; "
        "os.environ['OBSIDIAN_ROOT'] = tempfile.mkdtemp(); "
        "os.environ['EMBED_ENABLED'] = 'false'; "
        "os.environ['NOUZ_READ_ONLY'] = 'true'; "
        "import nouz_mcp.server as server; "
        "assert server.READ_ONLY is True; "
        "assert server.CACHE_WRITE is False"
    )
    subprocess.run([sys.executable, "-c", script], check=True)

    script_with_cache = (
        "import os, tempfile; "
        "os.environ['OBSIDIAN_ROOT'] = tempfile.mkdtemp(); "
        "os.environ['EMBED_ENABLED'] = 'false'; "
        "os.environ['NOUZ_READ_ONLY'] = 'true'; "
        "os.environ['NOUZ_CACHE_WRITE'] = 'true'; "
        "import nouz_mcp.server as server; "
        "assert server.READ_ONLY is True; "
        "assert server.CACHE_WRITE is True"
    )
    subprocess.run([sys.executable, "-c", script_with_cache], check=True)


def test_public_metadata_versions_match_package_version():
    server_json = json.loads(Path("server.json").read_text(encoding="utf-8"))
    assert server_json["version"] == __version__
    assert server_json["packages"][0]["version"] == __version__

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert "nouz_mcp._version.__version__" in pyproject


def test_calc_etalons_cli_is_packaged(tmp_path):
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'nouz-calc-etalons = "nouz_mcp.calc_etalons:main"' in pyproject

    legacy_script = Path("scripts/calc_etalons.py").read_text(encoding="utf-8")
    assert "nouz_mcp.calc_etalons" in legacy_script

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etalons:\n"
        "  - sign: X\n"
        "    text: Alpha domain text\n"
        "  - sign: Y\n"
        "    text: Beta domain text\n",
        encoding="utf-8",
    )

    assert calc_etalons.normalize_api_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    assert calc_etalons.load_etalon_texts(config_path) == {
        "X": "Alpha domain text",
        "Y": "Beta domain text",
    }
    assert "S" in calc_etalons.load_etalon_texts(None)


def test_public_metadata_json_files_parse():
    json.loads(Path("server.json").read_text(encoding="utf-8"))
    json.loads(Path("glama.json").read_text(encoding="utf-8"))


def test_frontmatter_parser_reads_yaml_and_body():
    raw = "---\ntype: quant\nlevel: 4\nsign: T\n---\nBody text"
    attrs, body = parse_frontmatter(raw)

    assert attrs["type"] == "quant"
    assert attrs["level"] == 4
    assert attrs["sign"] == "T"
    assert body.strip() == "Body text"


def test_frontmatter_parser_handles_bom_and_crlf():
    raw = "\ufeff---\r\nlevel: 4\r\nsign: S\r\n---\r\nBody text\r\n"
    attrs, body = parse_frontmatter(raw)

    assert attrs == {"level": 4, "sign": "S"}
    assert body == "Body text\r\n"


def test_metadata_dump_does_not_write_internal_fields():
    dumped = dump_metadata({
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


def test_explicit_tag_list_ignores_legacy_concepts():
    assert explicit_tag_list({"concepts": ["dirty", "legacy"]}) == []
    assert explicit_tag_list({"tags": [" graph ", "", None, "graph", "Graph_Tag"], "concepts": ["legacy"]}) == ["graph", "graph-tag"]
    assert explicit_tag_list({"tags": "manual"}) == ["manual"]
    assert canonical_tag("#Search Tag") == "search-tag"
    assert canonical_tag(" AI / Agent Context ") == "ai/agent-context"
    assert canonical_tag("#FF00A1") == ""
    assert canonical_tag("ff00a1") == ""
    assert canonical_tag("decade") == "decade"
    assert canonical_tag("2026") == ""

    report = explicit_tag_report({
        "tags": [
            " graph ",
            "Graph!",
            "AI / Agent Context",
            "#FF00A1",
            "https://example.com/topic",
            "2026",
        ]
    })
    assert report == {
        "tags": ["graph", "ai/agent-context"],
        "dropped": [
            {"value": "#FF00A1", "reason": "hex_color"},
            {"value": "https://example.com/topic", "reason": "url"},
            {"value": "2026", "reason": "no_letters"},
        ],
    }

    dumped = dump_metadata({"type": "quant", "level": 4, "sign": "S", "tags": [" graph ", "#search", "#FF00A1", "", None]})
    assert "tags:" in dumped
    assert "- graph" in dumped
    assert "- search" in dumped
    assert "#search" not in dumped
    assert "FF00A1" not in dumped


def test_extracted_helpers_match_server_contract(tmp_path):
    assert default_db_path(str(tmp_path), "cache.db") == str(tmp_path / "cache.db")
    assert default_db_path(str(tmp_path), "cache.db", "custom.db") == "custom.db"
    assert safe_path(str(tmp_path), "notes/a.md") == tmp_path / "notes" / "a.md"
    assert safe_path(str(tmp_path), "../outside.md") is None
    assert serialize("plain") == "plain"
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert mean_center({"a": [1.0, 3.0], "b": [3.0, 1.0]}) == {
        "a": [-1.0, 1.0],
        "b": [1.0, -1.0],
    }


def test_markdown_helpers_are_directly_usable():
    attrs, body = parse_frontmatter("---\ntype: quant\nlevel: 4\n---\nBody")
    assert attrs == {"type": "quant", "level": 4}
    assert body == "Body"
    assert parse_frontmatter("---\nplain separator\n---\nBody")[0] == {}

    synced = sync_parents_fields({
        "parents_meta": [{"entity": "Module", "link_type": "hierarchy"}],
    })
    assert synced["parents"] == ["Module"]

    dumped = dump_metadata({"type": "quant", "level": 4, "sign": "S", "content": "hidden"})
    assert "type: quant" in dumped
    assert "content:" not in dumped


def test_chunk_markdown_is_stable_and_heading_aware():
    text = "# Root\n\nAlpha paragraph.\n\n## Details\n\nBeta paragraph with enough words for a second block.\n"

    chunks = chunk_markdown(text, source_id="note.md", max_chars=45, overlap_chars=8)
    chunks_again = chunk_markdown(text, source_id="note.md", max_chars=45, overlap_chars=8)

    assert chunks == chunks_again
    assert [chunk["index"] for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0]["source_id"] == "note.md"
    assert chunks[0]["id"].startswith("chunk:")
    assert chunks[0]["chunker_version"] == 1
    assert chunks[0]["heading"] == "Root"
    assert chunks[-1]["heading"] == "Details"
    assert all(chunk["start_char"] < chunk["end_char"] for chunk in chunks)
    assert all(chunk["char_count"] <= 45 + 8 for chunk in chunks)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    assert all(chunk["text"] == normalized[chunk["start_char"]:chunk["end_char"]] for chunk in chunks)
    assert all(chunk["body_start_char"] >= chunk["start_char"] for chunk in chunks)
    assert all(chunk["body_end_char"] == chunk["end_char"] for chunk in chunks)
    assert all(chunk["body_hash"] for chunk in chunks)


def test_chunk_markdown_handles_empty_text_and_large_blocks():
    assert chunk_markdown("   \n\n", source_id="empty.md") == []

    text = "A" * 130
    chunks = chunk_markdown(text, source_id="large.md", max_chars=50, overlap_chars=10)

    assert len(chunks) == 3
    assert chunks[0]["start_char"] == 0
    assert chunks[1]["overlap_chars"] == 10
    assert chunks[1]["start_char"] == chunks[1]["body_start_char"] - 10
    assert chunks[1]["text"].startswith("A" * 10)
    assert chunks[-1]["end_char"] == 130


def test_chunk_markdown_ignores_headings_inside_fenced_code():
    text = "# Root\n\n```python\n# not a heading\n```\n\n## Real Heading\nBody\n"
    chunks = chunk_markdown(text, source_id="code.md", max_chars=80, overlap_chars=0)

    assert len(chunks) == 2
    assert chunks[0]["heading"] == "Root"
    assert "# not a heading" in chunks[0]["text"]
    assert chunks[1]["heading"] == "Real Heading"


def test_mode_helpers_are_directly_usable():
    rules = build_rules(lambda entity_type, parents: [{"entity_type": entity_type, "parents": parents}])

    assert rules["luca"]["reference_vectors"] is False
    assert rules["prizma"]["semantic_bridges"] is True
    assert rules["sloi"]["level_strict"] is True
    assert rules["sloi"]["hierarchy_check"]("artifact", []) == [{"entity_type": "artifact", "parents": []}]

    assert get_type_by_level(4) == "quant"
    assert get_type_by_level(99) == "artifact"
    assert get_level("quant", {"quant": 4}) == 4


def test_sign_helpers_are_directly_usable():
    artifact_by_name = {"note": "n", "news": "u"}
    configured_keywords = {"news": ["fresh"]}
    default_keywords = {"log": ["session"], "note": []}

    assert artifact_sign("update", "u", artifact_by_name) == "u"
    assert artifact_keywords("update", configured_keywords, default_keywords) == ["fresh"]
    assert determine_artifact_sign("session notes", {}, artifact_by_name, {}, default_keywords) == "l"
    assert determine_artifact_sign("fresh info", {}, artifact_by_name, configured_keywords, default_keywords) == "u"
    assert extract_artifact_sign_from_sign("lS", {"l"}) == "l"
    assert extract_core_sign_from_sign("lS", {"S"}, {"l"}) == "S"
    assert extract_core_sign_from_sign("lX", set(), {"l"}) == "X"
    assert dedupe_sign_chars("llSSl") == "lS"
    assert signs_share_core("aS", "Sb", {"S"}) is True


def test_current_config_contract_is_stable():
    assert server.DEFAULT_CONFIG["mode"] == "luca"
    assert server.DEFAULT_CONFIG["levels"] == {
        "core": 1,
        "pattern": 2,
        "module": 3,
        "quant": 4,
        "artifact": 5,
    }
    assert server.DEFAULT_CONFIG["thresholds"]["semantic_bridge_threshold"] == 0.55
    assert server.DEFAULT_ARTIFACT_SIGNS[0] == {
        "sign": "n",
        "name": "Note",
        "text": "Short note, observation, fragment.",
    }
    assert "requirements" in server.DEFAULT_ARTIFACT_KEYWORDS["specification"]


def test_sqlite_store_helpers_are_directly_usable(tmp_path):
    async def scenario():
        db_path = str(tmp_path / "nouz.db")
        await init_sqlite_store(
            db_path,
            reference_vectors=True,
            determine_artifact_sign=lambda content, meta: "n",
        )

        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
                tables = {row[0] for row in await cur.fetchall()}
            assert {"files", "links", "embeddings", "chunk_embeddings", "reference_vectors"}.issubset(tables)

            note_path = tmp_path / "note.md"
            note_path.write_text("body", encoding="utf-8")
            await db.executemany(
                "INSERT INTO files (path, type, level) VALUES (?, ?, ?)",
                [
                    ("A.md", "core", 1),
                    ("B.md", "module", 3),
                    ("C.md", "quant", 4),
                ],
            )
            await db.executemany(
                "INSERT INTO links (parent_path, child_path, link_type) VALUES (?, ?, ?)",
                [
                    ("A.md", "B.md", "hierarchy"),
                    ("B.md", "C.md", "hierarchy"),
                    ("Missing.md", "C.md", "semantic"),
                ],
            )
            await db.commit()

        assert await store_get_db_children(db_path, "A.md") == ["B.md", "C.md"]
        assert await store_list_hierarchy_child_paths(db_path, "A.md") == ["B.md"]
        assert await store_get_db_parents(db_path, "B.md") == [{"entity": "A", "link_type": "hierarchy"}]
        assert await store_check_cycle_exists(db_path, "C.md", "A.md") is True
        assert await store_check_cycle_exists(db_path, "A.md", "C.md") is False
        assert await store_find_orphaned_links(db_path) == [
            {"child": "C.md", "missing_parent": "Missing.md", "link_type": "semantic"}
        ]
        assert await store_find_entity_path_by_stem(db_path, "B") == "B.md"

        many_paths = [f"many-{idx}.md" for idx in range(1100)]
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                "INSERT INTO files (path, type, level, sign) VALUES (?, ?, ?, ?)",
                [(path, "quant", 4, "S") for path in many_paths],
            )
            await db.commit()
        summaries = await store_get_file_summaries(db_path, many_paths)
        assert len(summaries) == len(many_paths)
        assert summaries["many-1099.md"] == ("quant", 4, "S")

        await store_save_embedding(db_path, str(note_path), [1.0, 2.0])
        assert await store_load_embedding(db_path, str(note_path)) == [1.0, 2.0]
        assert await store_embedding_is_fresh(db_path, str(note_path)) is True
        chunks = chunk_markdown("Alpha\n\nBeta", source_id=str(note_path), max_chars=8, overlap_chars=2)
        await store_save_chunk_embeddings(db_path, str(note_path), chunks, [[1.0, 0.0], [0.0, 1.0]])
        stored_chunks = await store_list_chunk_embeddings(db_path)
        assert len(stored_chunks) == 2
        assert stored_chunks[0]["path"] == str(note_path)
        assert stored_chunks[0]["chunker_version"] == 1
        assert stored_chunks[0]["body_hash"]
        assert json.loads(stored_chunks[0]["embedding"]) == [1.0, 0.0]
        assert await store_chunk_embeddings_are_fresh(db_path, str(note_path)) is True
        await store_save_chunk_embeddings(db_path, str(note_path), chunks[:1], [[0.5, 0.5]])
        assert len(await store_list_chunk_embeddings(db_path, str(note_path))) == 1
        await store_save_reference_vector(db_path, "S", "systems", [0.1, 0.2])
        assert await store_load_reference_vectors(db_path) == {"S": [0.1, 0.2]}

        indexed_path = tmp_path / "indexed.md"
        await store_index_file(
            db_path,
            indexed_path,
            {
                "type": "quant",
                "level": 4,
                "sign": "S",
                "content": "Long body",
                "parents_meta": [{"entity": "A", "link_type": "hierarchy"}],
                "tags": ["graph"],
            },
            get_parents_meta=get_parents_meta,
            resolve_entity_path=lambda entity: asyncio.sleep(0, result=f"{entity}.md"),
        )
        assert await store_get_db_parents(db_path, str(indexed_path)) == [{"entity": "A", "link_type": "hierarchy"}]
        await store_save_embedding(db_path, str(indexed_path), [0.5, 0.5])
        candidates = await store_list_embedding_candidates(db_path)
        assert any(row[0] == str(indexed_path) and row[1] == "quant" for row in candidates)
        assert any(row[0] == str(indexed_path) and row[1] == "S" for row in await store_list_semantic_bridge_rows(db_path))
        assert any(row[0] == str(indexed_path) and json.loads(row[3]) == ["graph"] for row in await store_list_tag_bridge_rows(db_path))
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT tags FROM files WHERE path = ?", (str(indexed_path),)) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert json.loads(row[0]) == ["graph"]

        concepts_only_path = tmp_path / "concepts-only.md"
        await store_index_file(
            db_path,
            concepts_only_path,
            {
                "type": "quant",
                "level": 4,
                "sign": "S",
                "content": "Legacy body",
                "concepts": ["dirty", "legacy"],
            },
            get_parents_meta=get_parents_meta,
            resolve_entity_path=lambda entity: asyncio.sleep(0, result=f"{entity}.md"),
        )
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT tags FROM files WHERE path = ?", (str(concepts_only_path),)) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert json.loads(row[0]) == []
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                "UPDATE files SET sign = ? WHERE path = ?",
                [("S", "A.md"), ("S", "B.md")],
            )
            await db.commit()
        await store_save_embedding(db_path, "B.md", [0.0, 1.0])
        parent_candidates = await store_list_parent_embedding_candidates(db_path, "child.md", 4)
        assert any(row[0] == "B.md" and row[1] == "S" and row[2] == 3 for row in parent_candidates)
        assert any(row[0] == "A.md" and row[1] == "S" and row[2] == 1 for row in await store_list_core_anchor_candidates(db_path))

        second_indexed_path = tmp_path / "indexed2.md"
        await store_index_file(
            db_path,
            second_indexed_path,
            {
                "type": "quant",
                "level": 4,
                "sign": "D",
                "content": "Other body",
                "parents_meta": [{"entity": "A", "link_type": "hierarchy"}],
                "core_mix": {"S": 40.0, "D": 60.0},
            },
            get_parents_meta=get_parents_meta,
            resolve_entity_path=lambda entity: asyncio.sleep(0, result=f"{entity}.md"),
        )
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE files SET core_mix = ? WHERE path = ?",
                (json.dumps({"S": 80.0}), str(indexed_path)),
            )
            await db.commit()
        assert await store_aggregate_core_mix(db_path, "A.md", level_strict=False) == {"D": 30.0, "S": 60.0}
        recalc_rows = await store_list_sign_recalc_rows(db_path)
        assert any(row[0] == str(indexed_path) and row[2] == 4 for row in recalc_rows)
        await store_update_sign_recalc_rows(
            db_path,
            [("S", "auto", "S", "", str(indexed_path))],
            [(json.dumps({"S": 100.0}), str(indexed_path))],
        )
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT sign, sign_source, sign_auto, core_mix FROM files WHERE path = ?", (str(indexed_path),)) as cur:
                row = await cur.fetchone()
        assert row == ("S", "auto", "S", json.dumps({"S": 100.0}))
        assert await store_get_core_mix(db_path, str(indexed_path)) == {"S": 100.0}

    asyncio.run(scenario())


def test_link_helpers_are_directly_usable(tmp_path):
    meta = {
        "parents_meta": [
            "[[Module]]",
            {"entity": "Concept", "link_type": "semantic"},
            {"entity": "Analogy", "link_type": "analogy"},
        ],
        "parents": ["Fallback"],
    }
    assert get_parents_meta(meta) == [
        {"entity": "Module", "link_type": "hierarchy"},
        {"entity": "Concept", "link_type": "semantic"},
        {"entity": "Analogy", "link_type": "analogy"},
    ]

    (tmp_path / "Module.md").write_text("---\n---\n", encoding="utf-8")
    assert check_parents_exist(str(tmp_path), meta) == ["Concept", "Analogy"]


def test_semantics_embedding_helper_is_directly_usable():
    async def scenario():
        assert await get_embedding(
            "text",
            enabled=False,
            provider="openai",
            model="",
            api_url="http://127.0.0.1:1/v1",
            api_key="",
            cache={},
        ) == []

    asyncio.run(scenario())


def test_vault_io_read_file_with_metadata(tmp_path):
    async def scenario():
        note = tmp_path / "note.md"
        note.write_text("---\ntype: quant\nlevel: 4\n---\nBody", encoding="utf-8")

        data = await read_vault_file_with_metadata(
            note,
            parse_frontmatter=parse_frontmatter,
            serialize_value=serialize,
        )

        assert data["type"] == "quant"
        assert data["level"] == 4
        assert data["content"] == "Body"
        assert data["path"] == str(note)

    asyncio.run(scenario())


def test_write_file_use_cases_preserve_body_and_index(tmp_path):
    async def scenario():
        indexed = []
        note = tmp_path / "note.md"

        async def index_file(db_path: str, path: Path, data: dict):
            indexed.append((path.name, data))

        success, error = await write_file_with_metadata_use_case(
            "db.sqlite",
            tmp_path,
            note,
            "Body",
            {
                "type": "quant",
                "level": 4,
                "sign": "S",
                "parents_meta": [{"entity": "Parent", "link_type": "hierarchy"}],
            },
            sync_parents_fields=sync_parents_fields,
            resolve_entity_path=lambda db_path, entity: asyncio.sleep(0, result=None),
            check_cycle_exists=lambda db_path, parent_path, child_path: asyncio.sleep(0, result=False),
            dump_metadata=dump_metadata,
            write_text=write_vault_text,
            index_file=index_file,
        )

        assert (success, error) == (True, "")
        assert "type: quant" in await read_vault_text(note)
        assert "parents_meta:" in await read_vault_text(note)
        assert indexed[0][0] == "note.md"
        assert indexed[0][1]["content"] == "Body"

        cycle_note = tmp_path / "cycle.md"
        success, error = await write_file_with_metadata_use_case(
            "db.sqlite",
            tmp_path,
            cycle_note,
            "Cycle",
            {"parents_meta": [{"entity": "Parent", "link_type": "hierarchy"}]},
            sync_parents_fields=sync_parents_fields,
            resolve_entity_path=lambda db_path, entity: asyncio.sleep(0, result="parent.md"),
            check_cycle_exists=lambda db_path, parent_path, child_path: asyncio.sleep(0, result=True),
            dump_metadata=dump_metadata,
            write_text=write_vault_text,
            index_file=index_file,
        )
        assert (success, error) == (False, "cycle_detected")
        assert not cycle_note.exists()

        locked = tmp_path / "locked.md"
        locked.write_text("---\ntype: old\n---\nOriginal body", encoding="utf-8")
        captured = {}

        async def write_wrapper(path: Path, content: str, metadata: dict, db_path: str):
            captured["write_file"] = (path.name, content, metadata)
            return True, ""

        result = await write_file_use_case(
            "db.sqlite",
            locked,
            "locked.md",
            "ignored",
            {"type": "new"},
            content_lock=True,
            read_text=read_vault_text,
            split_frontmatter=split_frontmatter_raw,
            write_file_with_metadata=write_wrapper,
        )
        assert result == {"status": "ok", "path": "locked.md"}
        assert captured["write_file"] == ("locked.md", "Original body", {"type": "new"})

        result = await update_metadata_use_case(
            "db.sqlite",
            locked,
            "locked.md",
            {"type": "artifact"},
            read_text=read_vault_text,
            split_frontmatter=split_frontmatter_raw,
            write_file_with_metadata=write_wrapper,
        )
        assert result == {"status": "ok", "path": "locked.md", "body_preserved": True}
        assert captured["write_file"] == ("locked.md", "Original body", {"type": "artifact"})

    asyncio.run(scenario())


def test_read_file_use_case_indexes_and_warns(tmp_path):
    async def scenario():
        note = tmp_path / "note.md"
        calls = {"indexed": None}

        async def read_file_with_metadata(path: Path):
            return {
                "path": str(path),
                "content": "Body",
                "parents_meta": [{"entity": "Missing", "link_type": "hierarchy"}],
            }

        async def index_file(db_path: str, path: Path, data: dict):
            calls["indexed"] = (db_path, path.name, data["content"])

        result = await read_file_use_case(
            "db.sqlite",
            note,
            read_file_with_metadata=read_file_with_metadata,
            index_file=index_file,
            check_parents_exist=lambda data: ["Missing"],
        )

        assert calls["indexed"] == ("db.sqlite", "note.md", "Body")
        assert result["warnings"] == ["parent_missing: Missing"]

    asyncio.run(scenario())


def test_index_all_use_case_wires_layers(tmp_path):
    async def scenario():
        root = tmp_path / "vault"
        root.mkdir()
        (root / "keep.md").write_text("body", encoding="utf-8")
        hidden = root / ".obsidian"
        hidden.mkdir()
        (hidden / "skip.md").write_text("hidden", encoding="utf-8")

        calls = {"indexed": [], "saved": [], "chunks": [], "deleted": None}

        async def read_file(path: Path):
            return {"content": path.read_text(encoding="utf-8")}

        async def index_file(db_path: str, path: Path, data: dict):
            calls["indexed"].append(path.name)

        async def embedding_is_fresh(db_path: str, path: str):
            return False

        async def get_embedding(text: str):
            return [1.0, 2.0]

        async def save_embedding(db_path: str, path: str, vec: list[float]):
            calls["saved"].append((Path(path).name, vec))

        async def chunk_embeddings_are_fresh(db_path: str, path: str):
            return False

        async def save_chunk_embeddings(db_path: str, path: str, chunks: list[dict], vectors: list[list[float]]):
            assert chunks[0]["source_id"] == "keep.md"
            calls["chunks"].append((Path(path).name, len(chunks), vectors))

        async def delete_missing_entries(db_path: str, seen_paths: set[str]):
            calls["deleted"] = {Path(path).name for path in seen_paths}

        async def find_orphaned_links(db_path: str):
            return [{"child": "x", "missing_parent": "y", "link_type": "hierarchy"}]

        result = await index_all_files_use_case(
            "db.sqlite",
            root,
            excluded_dirs={".obsidian"},
            with_embeddings=True,
            embed_max_chars=10,
            read_file=read_file,
            index_file=index_file,
            is_meta_root=lambda path: False,
            embedding_is_fresh=embedding_is_fresh,
            get_embedding=get_embedding,
            save_embedding=save_embedding,
            delete_missing_entries=delete_missing_entries,
            find_orphaned_links=find_orphaned_links,
            chunk_markdown=chunk_markdown,
            chunk_embeddings_are_fresh=chunk_embeddings_are_fresh,
            save_chunk_embeddings=save_chunk_embeddings,
        )

        assert calls["indexed"] == ["keep.md"]
        assert calls["saved"] == [("keep.md", [1.0, 2.0])]
        assert calls["chunks"] == [("keep.md", 1, [[1.0, 2.0]])]
        assert calls["deleted"] == {"keep.md"}
        assert result == {
            "indexed": 1,
            "embedded": 1,
            "chunk_files": 1,
            "chunk_embedded": 1,
            "errors": 0,
            "orphans": [{"child": "x", "missing_parent": "y", "link_type": "hierarchy"}],
        }

    asyncio.run(scenario())


def test_process_orphans_use_case_wires_layers(tmp_path):
    async def scenario():
        disabled = await process_orphans_use_case(
            "db.sqlite",
            tmp_path,
            has_sign_auto=False,
            reference_vectors=False,
            embed_max_chars=100,
            parent_link_threshold=0.5,
            core_signs={"S"},
            excluded_dirs=set(),
            is_meta_root=lambda path: False,
            parse_frontmatter=parse_frontmatter,
            get_level_from_meta=lambda meta: 5,
            determine_sign=lambda content, meta, db_path, level: asyncio.sleep(0, result={}),
            get_parents_meta=get_parents_meta,
            get_embedding=lambda content: asyncio.sleep(0, result=[]),
            determine_core=lambda content, db_path: asyncio.sleep(0, result={}),
            list_parent_candidates=lambda db_path, own_path, level: asyncio.sleep(0, result=[]),
            find_temporary_anchor=lambda content, db_path, level: asyncio.sleep(0, result=None),
            cosine=cosine,
            write_file=lambda path, content, metadata, db_path: asyncio.sleep(0, result=(True, "")),
        )
        assert disabled == {"error": "Not available in luca mode"}

        root = tmp_path / "vault"
        root.mkdir()
        (root / "orphan.md").write_text("Body text", encoding="utf-8")
        hidden = root / ".obsidian"
        hidden.mkdir()
        (hidden / "skip.md").write_text("hidden", encoding="utf-8")

        captured = {"writes": []}

        async def determine_sign(content: str, meta: dict, db_path: str, level: int):
            assert meta["path"].endswith("orphan.md")
            assert level == 5
            return {
                "actual_sign": "n",
                "source": "auto",
                "artifact_sign": "n",
            }

        async def get_embedding(text: str):
            assert text == "Body text"
            return [1.0, 0.0]

        async def determine_core(content: str, db_path: str):
            return {"dominant": "S"}

        async def list_parent_candidates(db_path: str, own_path: str, level: int):
            assert own_path.endswith("orphan.md")
            assert level == 5
            return [
                ("Parent.md", "S", 3, json.dumps([1.0, 0.0])),
                ("Other.md", "D", 3, json.dumps([0.0, 1.0])),
            ]

        async def find_temporary_anchor(content: str, db_path: str, level: int):
            return "Anchor"

        async def write_file(path: Path, content: str, metadata: dict, db_path: str):
            captured["writes"].append((path.name, content, metadata))
            return True, ""

        result = await process_orphans_use_case(
            "db.sqlite",
            root,
            dry_run=False,
            auto_parents=True,
            limit=10,
            has_sign_auto=True,
            reference_vectors=True,
            embed_max_chars=100,
            parent_link_threshold=0.5,
            core_signs={"S", "D"},
            excluded_dirs={".obsidian"},
            is_meta_root=lambda path: False,
            parse_frontmatter=parse_frontmatter,
            get_level_from_meta=lambda meta: 5,
            determine_sign=determine_sign,
            get_parents_meta=get_parents_meta,
            get_embedding=get_embedding,
            determine_core=determine_core,
            list_parent_candidates=list_parent_candidates,
            find_temporary_anchor=find_temporary_anchor,
            cosine=cosine,
            write_file=write_file,
        )

        assert result["processed"] == 1
        orphan = result["orphans"][0]
        assert orphan["status"] == "ok"
        assert orphan["path"] == "orphan.md"
        assert orphan["hierarchy_parents"] == ["Parent"]
        assert orphan["temporary_parents"] == ["Anchor"]
        assert orphan["parents_auto"] is True
        assert captured["writes"][0][0] == "orphan.md"
        assert captured["writes"][0][2]["parents_meta"] == [
            {"entity": "Parent", "link_type": "hierarchy"},
            {"entity": "Anchor", "link_type": "temporary"},
        ]

    asyncio.run(scenario())


def test_add_entity_use_case_wires_layers(tmp_path):
    async def scenario():
        disabled = await add_entity_use_case(
            "db.sqlite",
            tmp_path / "disabled.md",
            "disabled.md",
            "body",
            has_sign_auto=False,
            reference_vectors=False,
            embed_max_chars=100,
            parent_link_threshold=0.5,
            core_signs={"S"},
            get_type_by_level=get_type_by_level,
            determine_sign=lambda content, meta, db_path, level: asyncio.sleep(0, result={}),
            get_embedding=lambda content: asyncio.sleep(0, result=[]),
            determine_core=lambda content, db_path: asyncio.sleep(0, result={}),
            list_parent_candidates=lambda db_path, own_path, level: asyncio.sleep(0, result=[]),
            find_temporary_anchor=lambda content, db_path, level: asyncio.sleep(0, result=None),
            cosine=cosine,
            write_file=lambda path, content, metadata, db_path: asyncio.sleep(0, result=(True, "")),
        )
        assert disabled == {"error": "Not available in luca mode. Use write_file instead."}

        captured = {"write": None}

        async def determine_sign(content: str, meta: dict, db_path: str, level: int):
            assert meta["type"] == "quant"
            assert level == 4
            return {
                "actual_sign": "nS",
                "source": "auto",
                "artifact_sign": "n",
            }

        async def get_embedding(text: str):
            return [1.0, 0.0]

        async def determine_core(content: str, db_path: str):
            return {"dominant": "S"}

        async def list_parent_candidates(db_path: str, own_path: str, level: int):
            assert own_path.endswith("created.md")
            return [
                ("Parent.md", "S", 3, json.dumps([1.0, 0.0])),
            ]

        async def find_temporary_anchor(content: str, db_path: str, level: int):
            return "Anchor"

        async def write_file(path: Path, content: str, metadata: dict, db_path: str):
            captured["write"] = (path.name, content, metadata)
            return True, ""

        result = await add_entity_use_case(
            "db.sqlite",
            tmp_path / "created.md",
            "created.md",
            "body",
            level=4,
            has_sign_auto=True,
            reference_vectors=True,
            embed_max_chars=100,
            parent_link_threshold=0.5,
            core_signs={"S", "D"},
            get_type_by_level=get_type_by_level,
            determine_sign=determine_sign,
            get_embedding=get_embedding,
            determine_core=determine_core,
            list_parent_candidates=list_parent_candidates,
            find_temporary_anchor=find_temporary_anchor,
            cosine=cosine,
            write_file=write_file,
        )

        assert result == {
            "status": "created",
            "path": "created.md",
            "level": 4,
            "sign": "nS",
            "artifact_sign": "n",
            "sign_source": "auto",
            "tags": [],
            "hierarchy_parents": ["Parent"],
            "temporary_parents": ["Anchor"],
        }
        assert captured["write"][0] == "created.md"
        assert "tags" not in captured["write"][2]
        assert captured["write"][2]["parents_meta"] == [
            {"entity": "Parent", "link_type": "hierarchy"},
            {"entity": "Anchor", "link_type": "temporary"},
        ]

    asyncio.run(scenario())


def test_recalc_core_mix_use_case_wires_layers():
    async def scenario():
        assert await recalc_core_mix_use_case(
            "db.sqlite",
            core_mix_enabled=False,
            mode_name="luca",
            level_strict=False,
            list_file_levels=lambda db_path: asyncio.sleep(0, result=[]),
            is_meta_root=lambda path: False,
            aggregate_core_mix=lambda db_path, path, child_level: asyncio.sleep(0, result=None),
            update_core_mixes=lambda db_path, updates: asyncio.sleep(0),
        ) == {"error": "This tool is not available in 'luca' mode."}

        captured = {"updates": None, "child_levels": []}

        async def list_file_levels(db_path: str):
            return [("parent.md", 3), ("artifact.md", 5), ("meta.md", 0)]

        async def aggregate_core_mix(db_path: str, path: str, child_level):
            captured["child_levels"].append((path, child_level))
            if path == "parent.md":
                return {"S": 75.0}
            return None

        async def update_core_mixes(db_path: str, updates: list[tuple[str, str]]):
            captured["updates"] = updates

        result = await recalc_core_mix_use_case(
            "db.sqlite",
            core_mix_enabled=True,
            mode_name="prizma",
            level_strict=True,
            list_file_levels=list_file_levels,
            is_meta_root=lambda path: path.name == "meta.md",
            aggregate_core_mix=aggregate_core_mix,
            update_core_mixes=update_core_mixes,
        )

        assert result == {"updated": 1}
        assert captured["child_levels"] == [("parent.md", 2)]
        assert captured["updates"] == [('{"S": 75.0}', "parent.md")]

    asyncio.run(scenario())


def test_recalc_signs_use_case_updates_expected_rows():
    async def scenario():
        disabled = await recalc_signs_use_case(
            "db.sqlite",
            dry_run=False,
            has_sign_auto=False,
            mode_name="luca",
            core_mix_enabled=False,
            core_signs={"S"},
            is_meta_root=lambda path: False,
            list_sign_rows=lambda db_path: asyncio.sleep(0, result=[]),
            determine_artifact_sign=lambda content, meta: "n",
            determine_core=lambda content, db_path: asyncio.sleep(0, result={}),
            read_artifact_sign=lambda path: "",
            update_sign_rows=lambda db_path, sign_updates, mix_updates: asyncio.sleep(0),
        )
        assert disabled == {
            "error": "This tool is not available in 'luca' mode. Use 'prizma' or 'sloi' mode for semantic classification."
        }

        captured = {"sign_updates": None, "mix_updates": None}

        async def list_sign_rows(db_path: str):
            return [
                ("empty.md", "", 4),
                ("meta.md", "meta content", 4),
                ("core.md", "core content", 1),
                ("artifact.md", "artifact content", 5),
                ("quant.md", "quant content", 4),
                ("module.md", "module content", 3),
            ]

        async def determine_core(content: str, db_path: str):
            return {
                "above_threshold": ["S"],
                "confident": True,
                "percentages": {"S": 70.0, "D": 30.0},
            }

        async def update_sign_rows(db_path: str, sign_updates: list, mix_updates: list):
            captured["sign_updates"] = sign_updates
            captured["mix_updates"] = mix_updates

        result = await recalc_signs_use_case(
            "db.sqlite",
            dry_run=False,
            has_sign_auto=True,
            mode_name="prizma",
            core_mix_enabled=True,
            core_signs={"S"},
            is_meta_root=lambda path: path == "meta.md",
            list_sign_rows=list_sign_rows,
            determine_artifact_sign=lambda content, meta: "l",
            determine_core=determine_core,
            read_artifact_sign=lambda path: "h" if path == "quant.md" else "",
            update_sign_rows=update_sign_rows,
        )

        assert result == {"updated": 3, "dry_run": False}
        assert captured["sign_updates"] == [
            ("l", "auto", "l", "l", "artifact.md"),
            ("hS", "auto", "S", "h", "quant.md"),
            ("S", "auto", "S", "", "module.md"),
        ]
        assert captured["mix_updates"] == [
            (json.dumps({"S": 70.0, "D": 30.0}), "quant.md"),
            (json.dumps({"S": 70.0, "D": 30.0}), "module.md"),
        ]

    asyncio.run(scenario())


def test_list_files_use_case_filters_indexed_files(tmp_path):
    async def scenario():
        root = tmp_path / "vault"
        root.mkdir()
        (root / "a.md").write_text("a", encoding="utf-8")
        (root / "b.md").write_text("b", encoding="utf-8")
        (root / "plain.md").write_text("plain", encoding="utf-8")
        hidden = root / ".obsidian"
        hidden.mkdir()
        (hidden / "skip.md").write_text("skip", encoding="utf-8")

        async def get_file_summaries(db_path: str, paths: list[str]):
            return {
                str(root / "a.md"): ("quant", 4, "S"),
                str(root / "b.md"): ("artifact", 5, "n"),
            }

        assert await list_files_use_case(
            "db.sqlite",
            root,
            excluded_dirs={".obsidian"},
            get_file_summaries=get_file_summaries,
        ) == [
            {"path": "a.md", "type": "quant", "level": 4, "sign": "S"},
            {"path": "b.md", "type": "artifact", "level": 5, "sign": "n"},
        ]

        assert await list_files_use_case(
            "db.sqlite",
            root,
            excluded_dirs={".obsidian"},
            filter_level=4,
            filter_sign="S",
            no_metadata=True,
            get_file_summaries=get_file_summaries,
        ) == [
            {"path": "a.md", "type": "quant", "level": 4, "sign": "S"},
            {"path": "plain.md", "type": None, "level": None, "sign": None},
        ]

    asyncio.run(scenario())


def test_suggest_parents_use_case_ranks_same_core_candidates(tmp_path):
    async def scenario():
        note = tmp_path / "child.md"
        note.write_text("---\nlevel: 4\n---\nChild content", encoding="utf-8")

        async def determine_core(content: str, db_path: str):
            return {"dominant": "S", "scores": {"S": 0.9}, "spread": 0.7}

        async def get_embedding(text: str):
            return [1.0, 0.0]

        async def list_embedding_candidates(db_path: str):
            return [
                (str(note), "quant", 4, "S", json.dumps([1.0, 0.0])),
                ("domain.md", "core", 1, "S", json.dumps([1.0, 0.0])),
                ("other.md", "core", 1, "D", json.dumps([0.0, 1.0])),
                ("broken.md", "core", 1, "S", "not-json"),
            ]

        result = await suggest_parents_use_case(
            str(note),
            "db.sqlite",
            top_n=2,
            embed_max_chars=100,
            core_signs={"S", "D"},
            determine_core=determine_core,
            get_embedding=get_embedding,
            list_embedding_candidates=list_embedding_candidates,
            parse_frontmatter=parse_frontmatter,
            cosine=cosine,
        )

        assert result["dominant_core"] == "S"
        assert [candidate["path"] for candidate in result["candidates"]] == ["domain.md", "other.md"]
        assert result["candidates"][0]["same_core"] is True
        assert result["candidates"][0]["recommended_link_type"] == "hierarchy"

    asyncio.run(scenario())


def test_suggest_parents_use_case_prefers_adjacent_level_when_scores_are_close(tmp_path):
    async def scenario():
        note = tmp_path / "child.md"
        note.write_text("---\nlevel: 4\n---\nChild content", encoding="utf-8")

        async def determine_core(content: str, db_path: str):
            return {"dominant": "S", "scores": {"S": 0.9}, "spread": 0.7}

        async def get_embedding(text: str):
            return [0.0]

        async def list_embedding_candidates(db_path: str):
            return [
                (str(note), "quant", 4, "S", json.dumps([1.0])),
                ("same_level.md", "quant", 4, "S", json.dumps([1.0])),
                ("child_level.md", "artifact", 5, "S", json.dumps([1.0])),
                ("near_module.md", "module", 3, "S", json.dumps([0.99])),
                ("perfect_core.md", "core", 1, "S", json.dumps([1.0])),
            ]

        result = await suggest_parents_use_case(
            str(note),
            "db.sqlite",
            top_n=3,
            embed_max_chars=100,
            core_signs={"S"},
            determine_core=determine_core,
            get_embedding=get_embedding,
            list_embedding_candidates=list_embedding_candidates,
            parse_frontmatter=parse_frontmatter,
            cosine=lambda own, other: other[0],
        )

        paths = [candidate["path"] for candidate in result["candidates"]]
        assert paths[:2] == ["near_module.md", "perfect_core.md"]
        assert "same_level.md" not in paths
        assert "child_level.md" not in paths

    asyncio.run(scenario())


def test_suggest_parents_use_case_reports_unavailable_embeddings(tmp_path):
    async def scenario():
        note = tmp_path / "child.md"
        note.write_text("Child content", encoding="utf-8")

        async def determine_core(content: str, db_path: str):
            return {"dominant": "S", "scores": {"S": 0.9}}

        async def get_embedding(text: str):
            return []

        result = await suggest_parents_use_case(
            str(note),
            "db.sqlite",
            top_n=3,
            embed_max_chars=100,
            core_signs={"S"},
            determine_core=determine_core,
            get_embedding=get_embedding,
            list_embedding_candidates=lambda db_path: asyncio.sleep(0, result=[]),
            parse_frontmatter=parse_frontmatter,
            cosine=cosine,
        )

        assert result["error"] == "Embeddings unavailable."
        assert result["dominant_core"] == "S"
        assert result["candidates"] == []

    asyncio.run(scenario())


def test_search_chunk_embeddings_use_case_ranks_stored_chunks():
    async def scenario():
        async def get_embedding(text: str):
            assert text == "needle"
            return [1.0, 0.0]

        async def list_chunk_embeddings(db_path: str, path: str):
            assert db_path == "db.sqlite"
            assert path == "note.md"
            return [
                {
                    "chunk_id": "chunk:far",
                    "chunker_version": 1,
                    "path": "note.md",
                    "index": 0,
                    "start_char": 0,
                    "end_char": 4,
                    "body_start_char": 0,
                    "body_end_char": 4,
                    "heading": "A",
                    "body_hash": "body-a",
                    "text_hash": "text-a",
                    "text": "far",
                    "embedding": json.dumps([0.0, 1.0]),
                },
                {
                    "chunk_id": "chunk:near",
                    "chunker_version": 1,
                    "path": "note.md",
                    "index": 1,
                    "start_char": 5,
                    "end_char": 10,
                    "body_start_char": 5,
                    "body_end_char": 10,
                    "heading": "B",
                    "body_hash": "body-b",
                    "text_hash": "text-b",
                    "text": "near",
                    "embedding": json.dumps([1.0, 0.0]),
                },
                {"chunk_id": "broken", "embedding": "not-json"},
            ]

        result = await search_chunk_embeddings_use_case(
            " needle ",
            "db.sqlite",
            top_k=1,
            path="note.md",
            embed_max_chars=20,
            get_embedding=get_embedding,
            list_chunk_embeddings=list_chunk_embeddings,
            cosine=cosine,
        )

        assert result["query"] == "needle"
        assert result["top_k"] == 1
        assert [match["chunk_id"] for match in result["matches"]] == ["chunk:near"]
        assert result["matches"][0]["score"] == 1.0
        assert result["matches"][0]["body_hash"] == "body-b"

        empty = await search_chunk_embeddings_use_case(
            "  ",
            "db.sqlite",
            embed_max_chars=20,
            get_embedding=get_embedding,
            list_chunk_embeddings=list_chunk_embeddings,
            cosine=cosine,
        )
        assert empty == {"error": "Empty query.", "matches": []}

    asyncio.run(scenario())


def test_suggest_tag_bridges_use_case_uses_canonical_explicit_tags():
    async def scenario():
        async def list_tag_bridge_rows(db_path: str):
            assert db_path == "db.sqlite"
            return [
                ("own.md", "S", "", json.dumps(["graph", "agent-context"])),
                ("same.md", "D", "", json.dumps(["Graph", "other"])),
                ("other.md", "E", "", json.dumps(["unrelated"])),
                ("bad.md", "E", "", "not-json"),
            ]

        bridges = await suggest_tag_bridges_use_case(
            "db.sqlite",
            "own.md",
            ["#graph", "Agent_Context"],
            list_tag_bridge_rows=list_tag_bridge_rows,
        )

        assert bridges == [
            {
                "entity": "same",
                "link_type": "tag",
                "strength": 0.333,
                "tags": ["graph"],
                "reason": "shared explicit tags: graph",
            }
        ]

    asyncio.run(scenario())


def test_suggest_tag_candidates_use_case_is_read_only_and_vocabulary_based():
    content = (
        "# Heading\n\nThis note is about agent context and recursive theory. #New_Tag #FF00A1\n"
        "```python\n#not-a-tag\n```\n"
    )
    chunks = chunk_markdown(content, source_id="own.md", max_chars=1200, overlap_chars=0)
    rows = [
        ("own.md", "S", "", json.dumps(["graph"])),
        ("agent.md", "S", "", json.dumps(["agent-context", "graph"])),
        ("theory.md", "D", "", json.dumps(["recursive-theory"])),
        ("broken.md", "D", "", "not-json"),
    ]

    candidates = suggest_tag_candidates_use_case(
        content,
        ["graph"],
        rows,
        chunks=chunks,
    )

    assert candidates == [
        {
            "tag": "new-tag",
            "source": "inline",
            "confidence": 0.9,
            "reason": "explicit inline hashtag in note body",
            "evidence": [
                {
                    "chunk_id": chunks[0]["id"],
                    "heading": "Heading",
                    "start_char": content.index("#New_Tag"),
                    "end_char": content.index("#New_Tag") + len("#New_Tag"),
                    "snippet": "# Heading This note is about agent context and recursive theory. #New_Tag #FF00A1 ```python #not-a-tag ```",
                }
            ],
        },
        {
            "tag": "agent-context",
            "source": "vocabulary",
            "confidence": 0.66,
            "reason": "content matches an existing YAML tag",
            "usage_count": 1,
            "example_entity": "agent",
            "evidence": [
                {
                    "chunk_id": chunks[0]["id"],
                    "heading": "Heading",
                    "start_char": content.index("agent context"),
                    "end_char": content.index("agent context") + len("agent context"),
                    "snippet": "# Heading This note is about agent context and recursive theory. #New_Tag #FF00A1 ```python #not-a-tag ```",
                }
            ],
        },
        {
            "tag": "recursive-theory",
            "source": "vocabulary",
            "confidence": 0.66,
            "reason": "content matches an existing YAML tag",
            "usage_count": 1,
            "example_entity": "theory",
            "evidence": [
                {
                    "chunk_id": chunks[0]["id"],
                    "heading": "Heading",
                    "start_char": content.index("recursive theory"),
                    "end_char": content.index("recursive theory") + len("recursive theory"),
                    "snippet": "# Heading This note is about agent context and recursive theory. #New_Tag #FF00A1 ```python #not-a-tag ```",
                }
            ],
        },
    ]


def test_suggest_metadata_use_case_wires_layers():
    async def scenario():
        calls = {"saved": None}

        async def embedding_is_fresh(db_path: str, path: str):
            return False

        async def get_embedding(text: str):
            assert text == "body with other"
            return [1.0, 0.0]

        async def save_embedding(db_path: str, path: str, vec: list[float]):
            calls["saved"] = (path, vec)

        async def load_embedding(db_path: str, path: str):
            return []

        async def determine_sign(content: str, meta: dict, db_path: str, level: int):
            assert meta["path"] == "child.md"
            assert level == 4
            return {
                "actual_sign": "D",
                "sign_auto": "D",
                "artifact_sign": "n",
                "source": "auto",
                "confident": True,
            }

        async def find_semantic_bridges(db_path: str, own_path: str, own_sign: str, own_vec: list[float], own_sign_source: str):
            assert (own_path, own_sign, own_vec, own_sign_source) == ("child.md", "D", [1.0, 0.0], "auto")
            return [{"entity": "Bridge", "link_type": "semantic"}]

        async def list_tag_bridge_rows(db_path: str):
            return [
                ("tagged.md", "S", "", json.dumps(["tag", "other"])),
                ("candidate.md", "S", "", json.dumps(["other"])),
            ]

        async def resolve_entity_path(db_path: str, entity: str):
            assert entity == "Parent"
            return "parent.md"

        async def check_cycle_exists(db_path: str, parent_path: str, child_path: str):
            assert (parent_path, child_path) == ("parent.md", "child.md")
            return True

        async def determine_core(content: str, db_path: str):
            return {
                "percentages": {"D": 80.0, "S": 20.0},
                "max_cosine": 0.9,
                "confident": True,
            }

        async def get_core_mix(db_path: str, path: str):
            assert path == "child.md"
            return {"S": 90.0}

        expected_chunks = chunk_markdown("body with other", source_id="child.md", max_chars=1200, overlap_chars=0)
        result = await suggest_metadata_use_case(
            "body with other",
            {
                "type": "quant",
                "level": 4,
                "sign": "M",
                "tags": ["tag"],
                "parents_meta": [{"entity": "Parent", "link_type": "hierarchy"}],
            },
            "db.sqlite",
            "child.md",
            reference_vectors=True,
            semantic_bridges_enabled=True,
            core_mix_enabled=True,
            has_sign_auto=True,
            core_signs={"S", "D"},
            get_parents_meta=get_parents_meta,
            determine_type=lambda content, meta: str(meta.get("type", "entity")).strip().lower() or "entity",
            embedding_is_fresh=embedding_is_fresh,
            get_embedding=get_embedding,
            save_embedding=save_embedding,
            load_embedding=load_embedding,
            determine_sign=determine_sign,
            get_level_from_meta=lambda meta: int(meta.get("level", 5)),
            find_semantic_bridges=find_semantic_bridges,
            list_tag_bridge_rows=list_tag_bridge_rows,
            check_hierarchy=lambda entity_type, parents: [{"type": "existing"}],
            resolve_entity_path=resolve_entity_path,
            check_cycle_exists=check_cycle_exists,
            determine_core=determine_core,
            get_core_mix=get_core_mix,
            chunk_markdown=chunk_markdown,
        )

        assert calls["saved"] == ("child.md", [1.0, 0.0])
        assert result["type"] == "quant"
        assert result["sign"] == "D"
        assert result["artifact_sign"] == "n"
        assert result["tags"] == ["tag"]
        assert result["tag_quality"] == {"tags": ["tag"], "dropped": []}
        assert result["tag_candidates"] == [
            {
                "tag": "other",
                "source": "vocabulary",
                "confidence": 0.69,
                "reason": "content matches an existing YAML tag",
                "usage_count": 2,
                "example_entity": "tagged",
                "evidence": [
                    {
                        "chunk_id": expected_chunks[0]["id"],
                        "heading": "",
                        "start_char": 10,
                        "end_char": 15,
                        "snippet": "body with other",
                    }
                ],
            }
        ]
        assert result["semantic_bridges"] == [
            {"entity": "Bridge", "link_type": "semantic", "proposed": True}
        ]
        assert result["tag_bridges"] == [
            {
                "entity": "tagged",
                "link_type": "tag",
                "strength": 0.5,
                "tags": ["tag"],
                "reason": "shared explicit tags: tag",
                "proposed": True,
            }
        ]
        assert result["candidate_tag_bridges"] == [
            {
                "entity": "candidate",
                "link_type": "tag",
                "strength": 1.0,
                "tags": ["other"],
                "reason": "shared explicit tags: other",
                "proposed": True,
                "requires_tag_acceptance": True,
            },
            {
                "entity": "tagged",
                "link_type": "tag",
                "strength": 0.5,
                "tags": ["other"],
                "reason": "shared explicit tags: other",
                "proposed": True,
                "requires_tag_acceptance": True,
            },
        ]
        assert any(error.get("type") == "cycle_error" for error in result["errors"])
        assert result["core_percentages"] == {"D": 80.0, "S": 20.0}
        assert result["max_cosine"] == 0.9
        assert result["confident"] is True
        assert result["metrics"] == {"drift_manual_vs_auto": True, "drift_auto_vs_core": True}
        assert result["warnings"][0]["type"] == "core_drift"
        assert "predominantly S (90.0%)" in result["warnings"][0]["message"]

    asyncio.run(scenario())
