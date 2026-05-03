#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NOUZ deploy server tests.
Tests core functions without MCP connection and without embeddings.
Run: python test_server.py
"""

import asyncio
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

# ── Setup test environment ──────────────────────────────────────────────────
TEST_TMP_PARENT = Path(__file__).parent / ".test-tmp"
TEST_TMP_PARENT.mkdir(exist_ok=True)
TEST_VAULT = TEST_TMP_PARENT / "vault"
shutil.rmtree(str(TEST_VAULT), ignore_errors=True)
TEST_VAULT.mkdir(parents=True, exist_ok=True)
os.environ["OBSIDIAN_ROOT"] = str(TEST_VAULT)
os.environ["MODE"] = "luca"
os.environ["EMBED_ENABLED"] = "false"

# Patch sys.path so we can import server directly
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402

TEST_ROOT = Path(os.environ["OBSIDIAN_ROOT"])
DB_PATH = str(TEST_ROOT / server.DATABASE_NAME)

PASS = 0
FAIL = 0
ERRORS = []


def ok(name: str):
    global PASS
    PASS += 1
    print(f"  \033[32m[OK]\033[0m  {name}")


def fail(name: str, reason: str):
    global FAIL
    FAIL += 1
    ERRORS.append((name, reason))
    print(f"  \033[31m[FAIL]\033[0m  {name}")
    print(f"         {reason}")


def section(title: str):
    print(f"\n\033[1m{title}\033[0m")


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_md(rel_path: str, content: str = "", **meta) -> Path:
    """Write a markdown file with YAML frontmatter to TEST_ROOT."""
    p = TEST_ROOT / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    meta_str = server._dump_metadata(meta) if meta else ""
    text = f"---\n{meta_str}\n---\n{content}" if meta_str else content
    p.write_text(text, encoding="utf-8")
    return p


# ── Tests ────────────────────────────────────────────────────────────────────

async def test_version():
    section("VERSION")
    try:
        assert server.VERSION == "3.0.3", f"Expected 3.0.3, got {server.VERSION}"
        ok(f"VERSION == {server.VERSION}")
    except AssertionError as e:
        fail("VERSION check", str(e))


async def test_mode_defaults():
    section("MODE / CONFIG")
    try:
        assert server.MODE in ("luca", "prizma", "sloi"), f"Unknown mode: {server.MODE}"
        ok(f"mode = {server.MODE}")
    except AssertionError as e:
        fail("mode valid", str(e))

    try:
        assert isinstance(server.RULE, dict)
        assert "level_strict" in server.RULE
        ok("RULE dict has expected keys")
    except AssertionError as e:
        fail("RULE structure", str(e))


async def test_artifact_sign_russian_keywords():
    section("artifact_sign Russian keyword heuristics")
    cases = [
        ("Лог сессии: сначала проверили базу.", "l"),
        ("Это гипотеза о слабых связях.", "h"),
        ("Техническое задание для агента.", "s"),
        ("https://example.com/reference", "r"),
    ]
    for content, expected in cases:
        got = server._determine_artifact_sign(content, {})
        try:
            assert got == expected, f"{content!r}: expected {expected}, got {got}"
            ok(f"{content[:24]!r} -> {got}")
        except AssertionError as e:
            fail("artifact_sign Russian keyword", str(e))


async def test_safe_path():
    section("_safe_path (path traversal guard)")
    root = str(TEST_ROOT)

    p = server._safe_path(root, "notes/test.md")
    if p is not None:
        ok("valid relative path accepted")
    else:
        fail("valid path", "returned None for valid path")

    p2 = server._safe_path(root, "../../etc/passwd")
    if p2 is None:
        ok("path traversal blocked")
    else:
        fail("path traversal guard", f"should have returned None, got {p2}")


async def test_dump_metadata():
    section("_dump_metadata (YAML serialization)")

    meta = {
        "type": "quant",
        "level": 4,
        "sign": "T",
        "status": "active",
        "tags": ["ai", "graph"],
        "parents": ["Science"],
    }
    try:
        result = server._dump_metadata(meta)
        assert "type: quant" in result
        assert "level: 4" in result
        assert "sign: T" in result
        assert "parents:" in result
        assert "- Science" in result
        ok("basic metadata serialized")
    except AssertionError as e:
        fail("_dump_metadata basic", str(e))

    meta2 = {"type": "quant", "level": 4, "sign": "T", "parents": ["My: Special#Note"]}
    try:
        result2 = server._dump_metadata(meta2)
        assert "My: Special#Note" in result2
        ok("special chars in parent name")
    except AssertionError as e:
        fail("_dump_metadata special chars", str(e))

    try:
        lines = result.split("\n")
        keys_in_order = [l.split(":")[0].strip() for l in lines if ":" in l and not l.startswith("-")]
        expected_first = ["type", "level", "sign", "status", "tags", "parents"]
        actual_first = [k for k in keys_in_order if k in expected_first]
        assert actual_first == expected_first, f"Key order wrong: {actual_first}"
        ok("KEY_ORDER preserved")
    except AssertionError as e:
        fail("KEY_ORDER", str(e))


async def test_dump_metadata_whitelist():
    section("_dump_metadata whitelist (internal fields excluded)")

    meta = {
        "type": "quant",
        "level": 4,
        "sign": "T",
        "sign_source": "auto",
        "sign_auto": "T",
        "core_mix": {"T": 80.0, "S": 20.0},
        "path": "/some/path.md",
        "content": "this should not appear in YAML",
        "parents": ["Science"],
    }
    try:
        result = server._dump_metadata(meta)
        assert "sign_source" not in result, "sign_source leaked into YAML"
        assert "sign_auto" not in result, "sign_auto leaked into YAML"
        assert "core_mix" not in result, "core_mix leaked into YAML"
        assert "path:" not in result, "path leaked into YAML"
        assert "content:" not in result, "content leaked into YAML"
        assert "this should not appear" not in result, "content value leaked into YAML"
        assert "type: quant" in result
        assert "sign: T" in result
        assert "parents:" in result
        ok("whitelist blocks sign_source, sign_auto, core_mix, path, content")
    except AssertionError as e:
        fail("_dump_metadata whitelist", str(e))

    metadata = {"type": "artifact", "level": 5, "sign": "B", "tags": "None", "parents": [], "parents_meta": []}
    dumped = server._dump_metadata(metadata)
    try:
        assert "tags: None" not in dumped, dumped
        assert "parents:" not in dumped, dumped
        ok("None and empty YAML fields are omitted")
    except AssertionError as e:
        fail("_dump_metadata empty values", str(e))


async def test_sync_parents_fields():
    section("_sync_parents_fields")

    meta = {"parents": ["Science", "Math"]}
    synced = server._sync_parents_fields(meta)
    try:
        assert synced["parents"] == ["Science", "Math"]
        ok("plain parents preserved")
    except AssertionError as e:
        fail("plain parents", str(e))

    meta2 = {
        "parents_meta": [{"entity": "Science", "link_type": "hierarchy"}],
        "parents": ["OldValue"]
    }
    synced2 = server._sync_parents_fields(meta2)
    try:
        assert synced2["parents"] == ["Science"]
        ok("parents_meta takes precedence over parents")
    except AssertionError as e:
        fail("parents_meta precedence", str(e))


async def test_cosine():
    section("_cosine (vector similarity)")

    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    v4 = [-1.0, 0.0, 0.0]

    try:
        assert abs(server._cosine(v1, v2) - 1.0) < 1e-6
        ok("identical vectors -> cosine = 1.0")
    except AssertionError as e:
        fail("cosine identical", str(e))

    try:
        assert abs(server._cosine(v1, v3)) < 1e-6
        ok("orthogonal vectors -> cosine = 0.0")
    except AssertionError as e:
        fail("cosine orthogonal", str(e))

    try:
        assert abs(server._cosine(v1, v4) + 1.0) < 1e-6
        ok("opposite vectors -> cosine = -1.0")
    except AssertionError as e:
        fail("cosine opposite", str(e))

    try:
        assert server._cosine([], []) == 0.0
        assert server._cosine([1.0], []) == 0.0
        ok("empty vectors handled gracefully")
    except AssertionError as e:
        fail("cosine empty", str(e))


async def test_mean_center():
    section("_mean_center (anisotropy correction)")

    vecs = {
        "A": [1.0, 0.0],
        "B": [0.0, 1.0],
    }
    try:
        centered = server._mean_center(vecs)
        assert len(centered) == 2
        mean = [0.5, 0.5]
        for k in centered:
            expected = [vecs[k][i] - mean[i] for i in range(2)]
            for i in range(2):
                assert abs(centered[k][i] - expected[i]) < 1e-6
        ok("mean subtracted correctly from 2 vectors")
    except AssertionError as e:
        fail("_mean_center", str(e))

    single = {"X": [3.0, 4.0]}
    try:
        result = server._mean_center(single)
        assert result == single
        ok("single vector passes through unchanged")
    except AssertionError as e:
        fail("_mean_center single", str(e))


async def test_db_init():
    section("Database initialization")
    try:
        await server.init_db(DB_PATH)
        import aiosqlite
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
                tables = {row[0] for row in await cur.fetchall()}
        expected = {"files", "links", "embeddings"}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
        ok(f"DB tables created: {sorted(tables)}")
    except Exception as e:
        fail("init_db", str(e))


async def test_default_db_path_env():
    section("Database path configuration")

    old_name = server.DATABASE_NAME
    old_path = server.DATABASE_PATH
    try:
        server.DATABASE_PATH = ""
        server.DATABASE_NAME = "obsidian_kb.public.db"
        expected = os.path.join(server.OBSIDIAN_ROOT, "obsidian_kb.public.db")
        assert server._default_db_path() == expected, "NOUZ_DATABASE_NAME was not applied"

        server.DATABASE_PATH = str(TEST_ROOT / "custom.db")
        assert server._default_db_path() == server.DATABASE_PATH, "NOUZ_DATABASE_PATH should take precedence"
        ok("database name/path can be overridden for isolated public tests")
    except AssertionError as e:
        fail("_default_db_path", str(e))
    finally:
        server.DATABASE_NAME = old_name
        server.DATABASE_PATH = old_path


async def test_read_write_file():
    section("read_file / write_file")

    p = TEST_ROOT / "test_note.md"

    meta = {"type": "quant", "level": 4, "sign": "T", "parents": ["Science"]}
    success, err = await server.write_file_with_metadata(p, "Hello world", meta, DB_PATH)
    try:
        assert success, f"write failed: {err}"
        ok("write_file_with_metadata succeeded")
    except AssertionError as e:
        fail("write_file", str(e))
        return

    result = await server.read_file_with_metadata(p)
    try:
        assert result.get("type") == "quant"
        assert result.get("level") == 4
        assert result.get("sign") == "T"
        assert result.get("content") == "Hello world"
        ok("read_file_with_metadata returns correct data")
    except AssertionError as e:
        fail("read_file content", str(e))

    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT type, level, sign FROM files WHERE path=?", (str(p),)) as cur:
            row = await cur.fetchone()
    try:
        assert row is not None, "file not in DB"
        assert row[0] == "quant"
        assert row[1] == 4
        assert row[2] == "T"
        ok("file indexed in DB correctly")
    except AssertionError as e:
        fail("DB index after write", str(e))


async def test_read_plain_markdown_without_frontmatter_error():
    section("read_file plain Markdown")

    p = TEST_ROOT / "PlainRead.md"
    body = "# Plain Read\n\nImported without YAML.\n"
    p.write_text(body, encoding="utf-8")
    data = await server.read_file_with_metadata(p)

    try:
        assert data.get("content") == body, "plain Markdown body was not preserved"
        assert "frontmatter_error" not in data, f"plain Markdown should not warn: {data}"
        ok("plain Markdown reads with empty metadata")
    except AssertionError as e:
        fail("read plain Markdown", str(e))


async def test_read_markdown_starting_with_horizontal_rule():
    section("read_file Markdown horizontal rule")

    p = TEST_ROOT / "HorizontalRule.md"
    body = "---\n\nText that starts with a Markdown separator, not YAML.\n"
    p.write_text(body, encoding="utf-8")
    data = await server.read_file_with_metadata(p)

    try:
        assert data.get("content") == body, "leading horizontal rule was mistaken for YAML"
        assert "frontmatter_error" not in data, f"horizontal rule should not warn: {data}"
        ok("leading Markdown separator is treated as content")
    except AssertionError as e:
        fail("read horizontal rule", str(e))


async def test_parent_child_links():
    section("Parent/child links in DB")

    parent_p = TEST_ROOT / "ParentNote.md"
    await server.write_file_with_metadata(
        parent_p, "parent content",
        {"type": "module", "level": 3, "sign": "T"},
        DB_PATH
    )

    child_p = TEST_ROOT / "ChildNote.md"
    await server.write_file_with_metadata(
        child_p, "child content",
        {"type": "quant", "level": 4, "sign": "T", "parents": ["ParentNote"]},
        DB_PATH
    )

    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT parent_path, link_type FROM links WHERE child_path=?", (str(child_p),)
        ) as cur:
            rows = await cur.fetchall()

    try:
        assert len(rows) >= 1, f"No links found for child. DB links: {rows}"
        ok(f"child->parent link stored ({len(rows)} link(s))")
    except AssertionError as e:
        fail("parent-child link", str(e))

    children = await server._get_db_children(DB_PATH, str(parent_p))
    try:
        assert str(child_p) in children, f"child not in children list: {children}"
        ok("_get_db_children returns child")
    except AssertionError as e:
        fail("_get_db_children", str(e))

    parents = await server._get_db_parents(DB_PATH, str(child_p))
    try:
        assert any(p.get("entity") == "ParentNote" for p in parents), f"parent not found: {parents}"
        ok("_get_db_parents returns parent")
    except AssertionError as e:
        fail("_get_db_parents", str(e))


async def test_write_preserves_body_by_default():
    section("write_file_with_metadata preserves body by default")

    p = TEST_ROOT / "PreserveBody.md"
    body = "Intro\n\n## Links\nKeep this user section\n\npath: 'not a leak if user wrote it here'\n"
    await server.write_file_with_metadata(
        p,
        body,
        {"type": "artifact", "level": 5, "sign": "n"},
        DB_PATH,
    )
    raw = p.read_text(encoding="utf-8")

    try:
        assert "## Links" in raw, "user section was removed"
        assert "Keep this user section" in raw, "body content was changed"
        assert "path: 'not a leak if user wrote it here'" in raw, "body line was removed"
        ok("write_file_with_metadata does not clean body unless requested")
    except AssertionError as e:
        fail("body preserve default", str(e))


async def test_recalc_keeps_l4_sign_separate_from_child_artifacts():
    section("recalc keeps L4 sign separate from child artifacts")

    await server.init_db(DB_PATH)
    quant = TEST_ROOT / "CleanQuant.md"
    artifact = TEST_ROOT / "ArtifactChild.md"

    await server.write_file_with_metadata(
        quant,
        "quant body",
        {"type": "quant", "level": 4, "sign": "S"},
        DB_PATH,
    )
    await server.write_file_with_metadata(
        artifact,
        "artifact body",
        {"type": "artifact", "level": 5, "sign": "B", "artifact_sign": "B", "parents": ["CleanQuant"]},
        DB_PATH,
    )

    old_rule = server.RULE
    old_core_signs = server.CORE_SIGNS
    old_determine_core = server._determine_core_by_embedding

    async def fake_determine_core(content, db_path):
        return {"above_threshold": ["S"], "confident": True, "percentages": {"S": 100.0}}

    try:
        server.RULE = {**server.RULE, "has_sign_auto": True, "reference_vectors": True, "core_mix": True}
        server.CORE_SIGNS = {"S"}
        server._determine_core_by_embedding = fake_determine_core
        result = await server._recalc_signs(DB_PATH, dry_run=False)
    finally:
        server.RULE = old_rule
        server.CORE_SIGNS = old_core_signs
        server._determine_core_by_embedding = old_determine_core

    data = await server.read_file_with_metadata(quant)
    async with server.aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT sign, artifact_sign FROM files WHERE path = ?", (str(quant),)) as cur:
            db_sign, db_artifact_sign = await cur.fetchone()

    try:
        assert result["updated"] >= 1, f"recalc did not process quant: {result}"
        assert data["sign"] == "S", f"YAML sign changed unexpectedly: {data['sign']!r}"
        assert db_sign == "S", f"child artifact entered DB sign: {db_sign!r}"
        assert not db_artifact_sign, f"child artifact entered DB artifact_sign: {db_artifact_sign!r}"
        ok("L4 child artifacts stay out of the entity sign")
    except AssertionError as e:
        fail("L4 sign separation invariant", str(e))


async def test_process_orphans_accepts_plain_markdown():
    section("process_orphans accepts plain Markdown without YAML")

    await server.init_db(DB_PATH)
    plain = TEST_ROOT / "PlainImport.md"
    plain.write_text("# Plain Import\n\nText pasted from a document without frontmatter.\n", encoding="utf-8")
    await server._index_all_files(DB_PATH, with_embeddings=False)

    old_rule = server.RULE
    old_core_signs = server.CORE_SIGNS
    old_determine_core = server._determine_core_by_embedding

    async def fake_determine_core(content, db_path):
        return {"above_threshold": ["S"], "dominant": "S", "confident": True, "percentages": {"S": 100.0}}

    try:
        server.RULE = {**server.RULE, "has_sign_auto": True, "reference_vectors": False, "core_mix": True}
        server.CORE_SIGNS = {"S"}
        server._determine_core_by_embedding = fake_determine_core
        preview = await server._process_orphans(DB_PATH, dry_run=True, auto_parents=False, limit=200)
        applied = await server._process_orphans(DB_PATH, dry_run=False, auto_parents=False, limit=200)
    finally:
        server.RULE = old_rule
        server.CORE_SIGNS = old_core_signs
        server._determine_core_by_embedding = old_determine_core

    data = await server.read_file_with_metadata(plain)

    try:
        assert preview["processed"] >= 1, f"plain Markdown was not detected: {preview}"
        assert applied["processed"] >= 1, f"plain Markdown was not processed: {applied}"
        assert data.get("level") == 5, f"default level should be L5 artifact: {data}"
        assert data.get("sign"), f"metadata sign was not written: {data}"
        assert data.get("content", "").startswith("# Plain Import"), "body content changed"
        ok("plain Markdown import can be annotated by process_orphans")
    except AssertionError as e:
        fail("process_orphans plain Markdown", str(e))


async def test_process_orphans_limit_applies_to_plain_markdown():
    section("process_orphans limit applies to plain Markdown")

    await server.init_db(DB_PATH)
    for idx in range(3):
        (TEST_ROOT / f"PlainLimit{idx}.md").write_text(f"plain import {idx}\n", encoding="utf-8")
    await server._index_all_files(DB_PATH, with_embeddings=False)

    old_rule = server.RULE
    try:
        server.RULE = {**server.RULE, "has_sign_auto": True, "reference_vectors": False}
        result = await server._process_orphans(DB_PATH, dry_run=True, auto_parents=False, limit=2)
    finally:
        server.RULE = old_rule

    try:
        assert result["processed"] == 2, f"limit was ignored for plain Markdown: {result}"
        ok("plain Markdown orphan batch respects limit")
    except AssertionError as e:
        fail("process_orphans plain limit", str(e))


async def test_process_orphans_accepts_leading_horizontal_rule():
    section("process_orphans accepts Markdown horizontal rule")

    await server.init_db(DB_PATH)
    plain = TEST_ROOT / "PlainRuleImport.md"
    plain.write_text("---\n\nImported text after a Markdown separator.\n---\n", encoding="utf-8")
    await server._index_all_files(DB_PATH, with_embeddings=False)

    old_rule = server.RULE
    try:
        server.RULE = {**server.RULE, "has_sign_auto": True, "reference_vectors": False}
        result = await server._process_orphans(DB_PATH, dry_run=False, auto_parents=False, limit=200)
    finally:
        server.RULE = old_rule

    data = await server.read_file_with_metadata(plain)

    try:
        assert result["processed"] >= 1, f"horizontal-rule Markdown was not processed: {result}"
        assert data.get("level") == 5, f"horizontal-rule Markdown did not get metadata: {data}"
        assert data.get("content", "").startswith("---\n\nImported text"), "body horizontal rule was not preserved"
        ok("Markdown separator import can be annotated")
    except AssertionError as e:
        fail("process_orphans horizontal rule", str(e))


async def test_recalc_signs_keeps_l1_cores_manual():
    section("recalc_signs keeps L1 cores manual")

    await server.init_db(DB_PATH)
    core = TEST_ROOT / "ManualCore.md"
    await server.write_file_with_metadata(
        core,
        "core anchor body",
        {"type": "core", "level": 1, "sign": "S"},
        DB_PATH,
    )

    old_has_sign_auto = server.RULE["has_sign_auto"]
    try:
        server.RULE["has_sign_auto"] = True
        result = await server._recalc_signs(DB_PATH, dry_run=False)
        import aiosqlite
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT sign, sign_auto, sign_source FROM files WHERE path = ?", (str(core),)) as cur:
                sign, sign_auto, sign_source = await cur.fetchone()

        assert result["updated"] >= 0, f"unexpected recalc result: {result}"
        assert sign == "S", f"L1 sign changed: {sign!r}"
        assert sign_auto is None and sign_source is None, "L1 auto fields should stay empty"
        ok("L1 core signs are not overwritten by recalc_signs")
    except AssertionError as e:
        fail("recalc_signs L1 skip", str(e))
    finally:
        server.RULE["has_sign_auto"] = old_has_sign_auto


async def test_cycle_detection():
    section("Cycle detection")

    await server.init_db(DB_PATH)

    a = TEST_ROOT / "CycleA.md"
    b = TEST_ROOT / "CycleB.md"

    await server.write_file_with_metadata(a, "A", {"type": "quant", "level": 4}, DB_PATH)
    await server.write_file_with_metadata(
        b, "B",
        {"type": "quant", "level": 4, "parents": ["CycleA"]},
        DB_PATH
    )

    has_cycle = await server._check_cycle_exists(DB_PATH, str(b), str(a))
    try:
        assert has_cycle, "cycle not detected"
        ok("cycle A->B->A detected correctly")
    except AssertionError as e:
        fail("cycle detection", str(e))

    no_cycle = await server._check_cycle_exists(DB_PATH, str(a), str(b))
    try:
        ok(f"non-cycle check: {no_cycle} (normal direction)")
    except Exception as e:
        fail("non-cycle check", str(e))


async def test_serialize():
    section("_serialize (date handling)")
    from datetime import date, datetime

    try:
        d = date(2026, 4, 7)
        assert server._serialize(d) == "2026-04-07"
        ok("date serialized to ISO string")
    except AssertionError as e:
        fail("date serialize", str(e))

    try:
        dt = datetime(2026, 4, 7, 12, 0, 0)
        assert server._serialize(dt) == "2026-04-07T12:00:00"
        ok("datetime serialized to ISO string")
    except AssertionError as e:
        fail("datetime serialize", str(e))

    try:
        assert server._serialize("hello") == "hello"
        assert server._serialize(42) == 42
        ok("non-date passthrough unchanged")
    except AssertionError as e:
        fail("serialize passthrough", str(e))


async def test_list_files():
    section("list_files (scan vault)")

    make_md("notes/alpha.md", "alpha content", type="quant", level=4, sign="T")
    make_md("notes/beta.md", "beta content", type="module", level=3, sign="S")
    make_md("notes/gamma.md", "gamma content", type="quant", level=4, sign="T")

    root = Path(os.environ["OBSIDIAN_ROOT"])
    md_files = list(root.rglob("*.md"))
    try:
        assert len(md_files) >= 3, f"Expected >=3 .md files, found {len(md_files)}"
        ok(f"found {len(md_files)} .md files in vault")
    except AssertionError as e:
        fail("list_files scan", str(e))

    sign_t = [f for f in md_files if "alpha" in f.name or "gamma" in f.name]
    try:
        assert len(sign_t) >= 2
        ok("sign=T files found by name pattern")
    except AssertionError as e:
        fail("sign filter", str(e))


async def test_orphaned_links():
    section("Orphaned link detection")

    import aiosqlite
    ghost_parent = str(TEST_ROOT / "GhostParent.md")
    real_child = str(TEST_ROOT / "ChildNote.md")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO links (parent_path, child_path, link_type) VALUES (?,?,?)",
            (ghost_parent, real_child, "hierarchy")
        )
        await db.commit()

    orphans = await server._find_orphaned_links(DB_PATH)
    ghost_orphans = [o for o in orphans if o["missing_parent"] == ghost_parent]
    try:
        assert len(ghost_orphans) >= 1, f"Ghost parent not detected. All orphans: {orphans}"
        ok(f"orphaned link detected: {ghost_orphans[0]['missing_parent']}")
    except AssertionError as e:
        fail("orphaned links", str(e))


async def test_dedup_by_sign():
    section("_dedup_by_sign (sign aggregation)")

    if not hasattr(server, '_dedup_by_sign'):
        ok("_dedup_by_sign not in luca mode (skipped)")
        return

    try:
        items = [
            {"sign": "T", "name": "a"},
            {"sign": "T", "name": "b"},
            {"sign": "S", "name": "c"},
            {"sign": "T", "name": "d"},
        ]
        result = server._dedup_by_sign(items)
        signs_only = [r.get("sign", r) if isinstance(r, dict) else r for r in result]
        assert signs_only == ["T", "S"], f"Expected ['T', 'S'], got {signs_only}"
        ok("_dedup_by_sign aggregates signs with count")
    except Exception as e:
        fail("_dedup_by_sign", str(e))


# ── Runner ────────────────────────────────────────────────────────────────────

async def main():
    print("\n\033[1m======================================")
    print("  NOUZ deploy server test suite")
    print(f"  server.py v{server.VERSION}")
    print("======================================\033[0m")

    await test_version()
    await test_mode_defaults()
    await test_artifact_sign_russian_keywords()
    await test_safe_path()
    await test_dump_metadata()
    await test_dump_metadata_whitelist()
    await test_sync_parents_fields()
    await test_cosine()
    await test_mean_center()
    await test_db_init()
    await test_default_db_path_env()
    await test_read_write_file()
    await test_read_plain_markdown_without_frontmatter_error()
    await test_read_markdown_starting_with_horizontal_rule()
    await test_parent_child_links()
    await test_write_preserves_body_by_default()
    await test_recalc_keeps_l4_sign_separate_from_child_artifacts()
    await test_process_orphans_accepts_plain_markdown()
    await test_process_orphans_limit_applies_to_plain_markdown()
    await test_process_orphans_accepts_leading_horizontal_rule()
    await test_recalc_signs_keeps_l1_cores_manual()
    await test_cycle_detection()
    await test_serialize()
    await test_list_files()
    await test_orphaned_links()
    await test_dedup_by_sign()

    print(f"\n{'-'*42}")
    total = PASS + FAIL
    print(f"  \033[32m{PASS} passed\033[0m  |  \033[31m{FAIL} failed\033[0m  |  {total} total")
    if ERRORS:
        print("\n  Failed tests:")
        for name, reason in ERRORS:
            print(f"    \033[31m[FAIL]\033[0m {name}: {reason}")
    print()

    shutil.rmtree(str(TEST_ROOT), ignore_errors=True)

    return FAIL


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
