"""SQLite schema and graph-link helpers for NOUZ."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiosqlite

from nouz_mcp.markdown import explicit_tag_list


logger = logging.getLogger("nouz")
ArtifactSignResolver = Callable[[str, Dict[str, Any]], str]
ParentsResolver = Callable[[Dict[str, Any]], List[Dict[str, Any]]]
EntityPathResolver = Callable[[str], Awaitable[Optional[str]]]


async def init_db(
    db_path: str,
    *,
    reference_vectors: bool,
    determine_artifact_sign: ArtifactSignResolver,
) -> None:
    """Create core SQLite tables and run lightweight migrations."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                type TEXT,
                sign TEXT,
                sign_manual TEXT,
                sign_auto TEXT,
                sign_source TEXT,
                artifact_sign TEXT,
                level INTEGER,
                status TEXT,
                content TEXT,
                updated TIMESTAMP,
                tags TEXT,
                core_mix TEXT
            );
            CREATE TABLE IF NOT EXISTS links (
                parent_path TEXT,
                child_path TEXT,
                link_type TEXT,
                PRIMARY KEY (parent_path, child_path, link_type)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_hierarchy_child
                ON links(child_path) WHERE link_type='hierarchy';
            CREATE TABLE IF NOT EXISTS embeddings (
                path TEXT PRIMARY KEY,
                embedding TEXT,
                updated TIMESTAMP,
                file_mtime REAL
            );
            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                chunk_id TEXT PRIMARY KEY,
                chunker_version INTEGER,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_char INTEGER NOT NULL,
                end_char INTEGER NOT NULL,
                body_start_char INTEGER,
                body_end_char INTEGER,
                heading TEXT,
                body_hash TEXT,
                text_hash TEXT,
                text TEXT,
                embedding TEXT,
                updated TIMESTAMP,
                file_mtime REAL
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_path
                ON chunk_embeddings(path);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chunk_embeddings_path_index
                ON chunk_embeddings(path, chunk_index);
        ''')

        if reference_vectors:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS reference_vectors (
                    sign TEXT PRIMARY KEY,
                    etalon_text TEXT,
                    embedding TEXT,
                    updated TIMESTAMP
                );
            ''')

        await db.commit()

    await migrate_artifact_sign(db_path, determine_artifact_sign=determine_artifact_sign)
    await migrate_chunk_embeddings(db_path)


async def migrate_artifact_sign(
    db_path: str,
    *,
    determine_artifact_sign: ArtifactSignResolver,
) -> None:
    """Add artifact_sign column to existing databases and populate L5 files."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(files)") as cur:
            columns = [row[1] for row in await cur.fetchall()]

        if "artifact_sign" not in columns:
            logger.info("Migration: adding artifact_sign column to files table")
            await db.execute("ALTER TABLE files ADD COLUMN artifact_sign TEXT")
            await db.commit()

            async with db.execute("SELECT path, content FROM files WHERE level = 5 AND content IS NOT NULL") as cur:
                l5_rows = await cur.fetchall()

            if l5_rows:
                updates = []
                for path, content in l5_rows:
                    art_sign = determine_artifact_sign(content, {})
                    updates.append((art_sign, path))

                await db.executemany(
                    "UPDATE files SET artifact_sign = ? WHERE path = ?",
                    updates,
                )
                await db.commit()
                logger.info(f"Migration: populated artifact_sign for {len(updates)} L5 files")


async def migrate_chunk_embeddings(db_path: str) -> None:
    """Add retrieval metadata columns to existing chunk embedding tables."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(chunk_embeddings)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if not columns:
            return

        migrations = {
            "chunker_version": "ALTER TABLE chunk_embeddings ADD COLUMN chunker_version INTEGER",
            "body_start_char": "ALTER TABLE chunk_embeddings ADD COLUMN body_start_char INTEGER",
            "body_end_char": "ALTER TABLE chunk_embeddings ADD COLUMN body_end_char INTEGER",
            "body_hash": "ALTER TABLE chunk_embeddings ADD COLUMN body_hash TEXT",
            "text_hash": "ALTER TABLE chunk_embeddings ADD COLUMN text_hash TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                await db.execute(statement)
        await db.commit()


async def index_file(
    db_path: str,
    file_path: Path,
    meta: Dict[str, Any],
    *,
    get_parents_meta: ParentsResolver,
    resolve_entity_path: EntityPathResolver,
) -> None:
    """Upsert one file record and refresh its indexed parent links."""
    parents_obj = get_parents_meta(meta)
    level = meta.get("level")
    if level == "":
        level = None

    yaml_sign = str(meta.get("sign", "")).strip() if meta.get("sign") else ""

    resolved_parents = []
    for parent in parents_obj:
        if isinstance(parent, dict):
            parent_entity = parent.get("entity", "")
            link_type = parent.get("link_type", "hierarchy")
            if parent_entity:
                parent_path = await resolve_entity_path(parent_entity)
                if parent_path:
                    resolved_parents.append((parent_path, link_type))
                else:
                    resolved_parents.append((parent_entity, link_type))

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO files
               (path, type, sign, sign_manual, sign_auto, sign_source, artifact_sign, level, status, content, updated, tags, core_mix)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                str(file_path),
                meta.get("type", ""),
                meta.get("sign", ""),
                yaml_sign if yaml_sign else None,
                None,
                None,
                meta.get("artifact_sign", ""),
                level,
                meta.get("status", "active"),
                meta.get("content", "")[:2000],
                datetime.now().isoformat(),
                json.dumps(explicit_tag_list(meta), ensure_ascii=False),
                json.dumps(meta.get("core_mix", {}), ensure_ascii=False) if meta.get("core_mix") else None,
            ),
        )

        await db.execute("DELETE FROM links WHERE child_path = ?", (str(file_path),))

        for parent_path, link_type in resolved_parents:
            await db.execute(
                "INSERT OR REPLACE INTO links (parent_path, child_path, link_type) VALUES (?, ?, ?)",
                (parent_path, str(file_path), link_type),
            )

        await db.commit()


async def aggregate_core_mix(
    db_path: str,
    parent_path: str,
    *,
    level_strict: bool,
    child_level: Optional[int] = None,
) -> Optional[Dict[str, float]]:
    """Average core_mix values from hierarchy children of a parent."""
    mixes = []

    async with aiosqlite.connect(db_path) as db:
        if child_level is not None and level_strict:
            async with db.execute(
                'SELECT f.core_mix FROM files f '
                'JOIN links l ON f.path = l.child_path '
                'WHERE l.parent_path = ? AND l.link_type = "hierarchy" AND f.level = ? AND f.core_mix IS NOT NULL',
                (parent_path, child_level),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                'SELECT f.core_mix FROM files f '
                'JOIN links l ON f.path = l.child_path '
                'WHERE l.parent_path = ? AND l.link_type = "hierarchy" AND f.core_mix IS NOT NULL',
                (parent_path,),
            ) as cur:
                rows = await cur.fetchall()

    for core_mix_json, in rows:
        if not core_mix_json:
            continue
        try:
            mix = json.loads(core_mix_json)
            if isinstance(mix, dict) and mix:
                mixes.append(mix)
        except Exception:
            continue

    if not mixes:
        return None

    all_keys = set()
    for mix in mixes:
        all_keys.update(mix.keys())
    result: Dict[str, float] = {}
    for key in all_keys:
        vals = [mix.get(key, 0.0) for mix in mixes]
        result[key] = round(sum(vals) / len(vals), 1)
    return result


async def delete_missing_index_entries(db_path: str, seen_paths: set[str]) -> None:
    """Remove index rows for paths not seen in the current vault scan."""
    if not seen_paths:
        return

    placeholders = ",".join("?" for _ in seen_paths)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"DELETE FROM links WHERE child_path NOT IN ({placeholders})",
            tuple(seen_paths),
        )
        await db.execute(
            f"DELETE FROM files WHERE path NOT IN ({placeholders})",
            tuple(seen_paths),
        )
        await db.execute(
            f"DELETE FROM embeddings WHERE path NOT IN ({placeholders})",
            tuple(seen_paths),
        )
        await db.execute(
            f"DELETE FROM chunk_embeddings WHERE path NOT IN ({placeholders})",
            tuple(seen_paths),
        )
        await db.commit()


async def save_chunk_embeddings(
    db_path: str,
    file_path: str,
    chunks: List[Dict[str, Any]],
    vectors: List[List[float]],
) -> None:
    """Replace stored chunk embeddings for one source file."""
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have the same length")

    try:
        mtime = Path(file_path).stat().st_mtime
    except Exception:
        mtime = None

    rows = []
    updated = datetime.now().isoformat()
    for chunk, vec in zip(chunks, vectors):
        if not vec:
            continue
        rows.append(
            (
                str(chunk["id"]),
                int(chunk.get("chunker_version", 1)),
                file_path,
                int(chunk["index"]),
                int(chunk["start_char"]),
                int(chunk["end_char"]),
                int(chunk.get("body_start_char", chunk["start_char"])),
                int(chunk.get("body_end_char", chunk["end_char"])),
                str(chunk.get("heading", "")),
                str(chunk.get("body_hash", "")),
                str(chunk.get("text_hash", "")),
                str(chunk.get("text", "")),
                json.dumps(vec),
                updated,
                mtime,
            )
        )

    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM chunk_embeddings WHERE path = ?", (file_path,))
        if rows:
            await db.executemany(
                '''INSERT OR REPLACE INTO chunk_embeddings
                   (chunk_id, chunker_version, path, chunk_index, start_char, end_char, body_start_char,
                    body_end_char, heading, body_hash, text_hash, text, embedding, updated, file_mtime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                rows,
            )
        await db.commit()


async def chunk_embeddings_are_fresh(
    db_path: str,
    file_path: str,
    *,
    chunker_version: int = 1,
    tolerance_seconds: float = 1.0,
) -> bool:
    """Return True when stored chunk embeddings match source mtime and chunker version."""
    try:
        current_mtime = Path(file_path).stat().st_mtime
    except Exception:
        return False

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            '''SELECT COUNT(*), MIN(file_mtime), MAX(file_mtime),
                      MIN(chunker_version), MAX(chunker_version)
               FROM chunk_embeddings WHERE path = ?''',
            (file_path,),
        ) as cur:
            row = await cur.fetchone()

    if not row or row[0] == 0 or row[1] is None or row[2] is None:
        return False
    if row[3] != chunker_version or row[4] != chunker_version:
        return False
    return (
        abs(float(row[1]) - current_mtime) < tolerance_seconds
        and abs(float(row[2]) - current_mtime) < tolerance_seconds
    )


async def list_chunk_embeddings(
    db_path: str,
    path: str = "",
) -> List[Dict[str, Any]]:
    """Return stored chunk embeddings, optionally limited to one file path."""
    async with aiosqlite.connect(db_path) as db:
        if path:
            async with db.execute(
                '''SELECT chunk_id, chunker_version, path, chunk_index, start_char, end_char,
                          body_start_char, body_end_char, heading, body_hash, text_hash, text, embedding
                   FROM chunk_embeddings
                   WHERE path = ? AND embedding IS NOT NULL
                   ORDER BY chunk_index''',
                (path,),
            ) as cur:
                rows = await cur.fetchall()
                return [_chunk_embedding_row(row) for row in rows]

        async with db.execute(
            '''SELECT chunk_id, chunker_version, path, chunk_index, start_char, end_char,
                      body_start_char, body_end_char, heading, body_hash, text_hash, text, embedding
               FROM chunk_embeddings
               WHERE embedding IS NOT NULL
               ORDER BY path, chunk_index'''
        ) as cur:
            rows = await cur.fetchall()
            return [_chunk_embedding_row(row) for row in rows]


def _chunk_embedding_row(row: tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "chunk_id": row[0],
        "chunker_version": row[1],
        "path": row[2],
        "index": row[3],
        "start_char": row[4],
        "end_char": row[5],
        "body_start_char": row[6],
        "body_end_char": row[7],
        "heading": row[8],
        "body_hash": row[9],
        "text_hash": row[10],
        "text": row[11],
        "embedding": row[12],
    }


async def list_file_levels_desc(db_path: str) -> List[tuple[str, Optional[int]]]:
    """Return indexed file paths ordered from deeper levels toward roots."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT path, level FROM files ORDER BY level DESC") as cur:
            return await cur.fetchall()


async def list_sign_recalc_rows(db_path: str) -> List[tuple[str, str, Optional[int]]]:
    """Return indexed file rows needed by recalc_signs."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT path, content, level FROM files") as cur:
            return await cur.fetchall()


async def get_file_summaries(db_path: str, path_list: List[str]) -> Dict[str, tuple[str, Optional[int], str]]:
    """Return type/level/sign tuples for indexed paths."""
    if not path_list:
        return {}

    placeholders = ",".join("?" for _ in path_list)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            f"SELECT path, type, level, sign FROM files WHERE path IN ({placeholders})",
            path_list,
        ) as cur:
            rows = await cur.fetchall()
    return {row[0]: row[1:] for row in rows}


async def list_embedding_candidates(db_path: str) -> List[tuple[str, str, Optional[int], str, str]]:
    """Return indexed files with stored embeddings for parent/bridge candidates."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT f.path, f.type, f.level, f.sign, e.embedding FROM files f '
            'LEFT JOIN embeddings e ON f.path = e.path '
            'WHERE e.embedding IS NOT NULL'
        ) as cur:
            return await cur.fetchall()


async def list_parent_embedding_candidates(
    db_path: str,
    own_path: str,
    child_level: int,
) -> List[tuple[str, str, Optional[int], str]]:
    """Return possible hierarchy parents with embeddings for one child level."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT f.path, f.sign, f.level, e.embedding FROM files f '
            'LEFT JOIN embeddings e ON f.path = e.path '
            'WHERE e.embedding IS NOT NULL AND f.path <> ? '
            'AND COALESCE(f.sign, "") <> "" AND f.level < ?',
            (own_path, child_level),
        ) as cur:
            return await cur.fetchall()


async def list_hierarchy_child_paths(db_path: str, parent_path: str) -> List[str]:
    """Return direct hierarchy child paths for one parent."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT child_path FROM links WHERE parent_path = ? AND link_type = "hierarchy"',
            (parent_path,),
        ) as cur:
            rows = await cur.fetchall()
    return [row[0] for row in rows]


async def list_core_anchor_candidates(db_path: str) -> List[tuple[str, str, Optional[int]]]:
    """Return indexed L1/L2 files that can act as temporary domain anchors."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT path, sign, level FROM files WHERE level IN (1, 2) AND sign IS NOT NULL"
        ) as cur:
            return await cur.fetchall()


async def list_semantic_bridge_rows(db_path: str) -> List[tuple[str, str, str, str, str]]:
    """Return indexed embedding rows used to suggest full-text semantic bridges."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT f.path, f.sign, f.sign_source, f.artifact_sign, e.embedding "
            "FROM files f JOIN embeddings e ON f.path = e.path "
            "WHERE e.embedding IS NOT NULL"
        ) as cur:
            return await cur.fetchall()


async def load_reference_vectors(db_path: str) -> Dict[str, List[float]]:
    """Load calibrated core reference vectors from SQLite."""
    vectors: Dict[str, List[float]] = {}
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT sign, embedding FROM reference_vectors") as cur:
            rows = await cur.fetchall()
    for sign, emb_json in rows:
        try:
            vectors[sign] = json.loads(emb_json)
        except Exception:
            continue
    return vectors


async def save_reference_vector(db_path: str, sign: str, text: str, vec: List[float]) -> None:
    """Store one calibrated core reference vector."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO reference_vectors (sign, etalon_text, embedding, updated) VALUES (?,?,?,?)",
            (sign, text[:2000], json.dumps(vec), datetime.now().isoformat()),
        )
        await db.commit()


async def find_entity_path_by_stem(db_path: str, entity_name: str) -> Optional[str]:
    """Resolve an indexed Markdown path by filename stem."""
    suffix_fwd = f"/{entity_name}.md"
    suffix_bck = f"\\{entity_name}.md"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT path FROM files WHERE path = ? OR path LIKE ? OR path LIKE ?",
            (f"{entity_name}.md", f"%{suffix_fwd}", f"%{suffix_bck}"),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def update_core_mixes(db_path: str, updates: List[tuple[str, str]]) -> None:
    """Persist computed core_mix JSON values for indexed files."""
    if not updates:
        return
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "UPDATE files SET core_mix = ? WHERE path = ?",
            updates,
        )
        await db.commit()


async def update_sign_recalc_rows(
    db_path: str,
    sign_updates: List[tuple[str, str, str, str, str]],
    mix_updates: List[tuple[str, str]],
) -> None:
    """Persist sign/sign_source/sign_auto/artifact_sign and optional core_mix updates."""
    if not sign_updates and not mix_updates:
        return
    async with aiosqlite.connect(db_path) as db:
        if sign_updates:
            await db.executemany(
                "UPDATE files SET sign = ?, sign_source = ?, sign_auto = ?, artifact_sign = ? WHERE path = ?",
                sign_updates,
            )
        if mix_updates:
            await db.executemany(
                "UPDATE files SET core_mix = ? WHERE path = ?",
                mix_updates,
            )
        await db.commit()


async def get_db_children(db_path: str, parent_path: str, visited: Optional[set] = None) -> List[str]:
    """Return hierarchy descendants for a parent path."""
    if visited is None:
        visited = set()
    if parent_path in visited:
        return []
    visited.add(parent_path)

    children = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT child_path FROM links WHERE parent_path = ? AND link_type = "hierarchy"',
            (parent_path,),
        ) as cur:
            rows = await cur.fetchall()
    for (child_path,) in rows:
        children.append(child_path)
        children.extend(await get_db_children(db_path, child_path, visited))
    return children


async def check_cycle_exists(db_path: str, new_parent: str, new_child: str) -> bool:
    """Return True if adding new_parent -> new_child would create a cycle."""
    visited = set()
    stack = [new_parent]
    while stack:
        current = stack.pop()
        if current == new_child:
            return True
        if current in visited:
            continue
        visited.add(current)

        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                'SELECT parent_path FROM links WHERE child_path = ? AND link_type = "hierarchy"',
                (current,),
            ) as cur:
                rows = await cur.fetchall()
        for (parent_path,) in rows:
            if parent_path:
                stack.append(parent_path)
    return False


async def get_db_parents(db_path: str, file_path: str) -> List[Dict[str, str]]:
    """Return parent records for one file path."""
    parents = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT parent_path, link_type FROM links WHERE child_path = ?',
            (file_path,),
        ) as cur:
            rows = await cur.fetchall()
    for parent_path, link_type in rows:
        parents.append({
            "entity": Path(parent_path).stem,
            "link_type": link_type,
        })
    return parents


async def find_orphaned_links(db_path: str) -> List[Dict[str, str]]:
    """Return links whose parent path is not present in the files table."""
    orphans = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('''
            SELECT l.child_path, l.parent_path, l.link_type
            FROM links l
            LEFT JOIN files f ON l.parent_path = f.path
            WHERE f.path IS NULL
        ''') as cur:
            rows = await cur.fetchall()
        for child_path, parent_path, link_type in rows:
            orphans.append({
                "child": child_path,
                "missing_parent": parent_path,
                "link_type": link_type,
            })
    return orphans


async def save_embedding(db_path: str, file_path: str, vec: List[float]) -> None:
    """Store an embedding vector with the source file modification time."""
    try:
        mtime = Path(file_path).stat().st_mtime
    except Exception:
        mtime = None
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'INSERT OR REPLACE INTO embeddings (path, embedding, updated, file_mtime) VALUES (?,?,?,?)',
            (file_path, json.dumps(vec), datetime.now().isoformat(), mtime),
        )
        await db.commit()


async def load_embedding(db_path: str, file_path: str) -> List[float]:
    """Load a stored embedding vector for a file path."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT embedding FROM embeddings WHERE path=?",
            (str(file_path),),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except Exception:
        return []


async def embedding_is_fresh(db_path: str, file_path: str, *, tolerance_seconds: float = 1.0) -> bool:
    """Return True when the stored embedding mtime matches the file mtime."""
    try:
        current_mtime = Path(file_path).stat().st_mtime
    except Exception:
        return False
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT file_mtime FROM embeddings WHERE path=?',
            (file_path,),
        ) as cur:
            row = await cur.fetchone()
    if not row or row[0] is None:
        return False
    return abs(float(row[0]) - current_mtime) < tolerance_seconds


async def get_core_mix(db_path: str, file_path: str) -> Optional[Dict[str, float]]:
    """Load stored core_mix for a file path."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT core_mix FROM files WHERE path = ?",
            (file_path,),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        mix = json.loads(row[0])
    except Exception:
        return None
    return mix if isinstance(mix, dict) and mix else None
