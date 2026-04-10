#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nouz — Unified MCP Server for Obsidian. v2.1.2

Three modes:
- luca: Graph-based, level is for display only, no semantic classification
- prizma: Graph-based with semantic bridges and core_mix
- sloi: Strict 5-level hierarchy with semantic classification
"""

VERSION = "2.1.2"

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import yaml
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

import aiofiles
import frontmatter
import aiohttp
import aiosqlite
from mcp.server import Server
import mcp.server.stdio
from mcp import types


# ============================================================================
# Mode Configuration
# ============================================================================

DEFAULT_CONFIG = {
    "profiles": {
        "default": {
            "mode": "luca",
            "etalons": []
        }
    },
    "levels": {
        "core": 1,
        "pattern": 2,
        "module": 3,
        "quant": 4,
        "artifact": 5
    },
    "thresholds": {
        "sign_spread": 0.05,
        "confident_cosine": 0.75,
        "pattern_second_sign_threshold": 30.0,
        "semantic_bridge_threshold": 0.55
    }
}

RULES = {
    "luca": {
        "description": "Graph-based, level is for display only",
        "level_strict": False,
        "semantic_bridges": False,
        "reference_vectors": False,
        "core_mix": False,
        "has_level_field": True,
        "has_sign_auto": False,
        "hierarchy_check": lambda et, pa: [],
    },
    "prizma": {
        "description": "Graph-based with semantic bridges",
        "level_strict": False,
        "semantic_bridges": True,
        "reference_vectors": True,
        "core_mix": True,
        "has_level_field": True,
        "has_sign_auto": True,
        "hierarchy_check": lambda et, pa: [],
    },
    "sloi": {
        "description": "Strict 5-level hierarchy",
        "level_strict": True,
        "semantic_bridges": True,
        "reference_vectors": True,
        "core_mix": True,
        "has_level_field": True,
        "has_sign_auto": True,
        "hierarchy_check": lambda et, pa: _check_hierarchy_strict(et, pa),
    }
}

def load_config() -> Dict[str, Any]:
    config_path = Path(__file__).parent / "config.yaml"
    profile_name = os.getenv("PROFILE", "default")
    
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            logging.info(f"Loaded config from {config_path}")
            
            profiles = config.get("profiles", {})
            if profiles and profile_name in profiles:
                profile = profiles[profile_name]
                config["mode"] = profile.get("mode", config.get("mode", "luca"))
                config["etalons"] = profile.get("etalons", config.get("etalons", DEFAULT_CONFIG["profiles"]["default"]["etalons"]))
                logging.info(f"Using profile: {profile_name}")
            elif "etalons" not in config and "profiles" not in config:
                config["etalons"] = DEFAULT_CONFIG["profiles"]["default"]["etalons"]
            
            return config
        except Exception as e:
            logging.warning(f"Failed to load config: {e}, using defaults")
    
    default_profile = DEFAULT_CONFIG["profiles"].get(profile_name, DEFAULT_CONFIG["profiles"]["default"])
    return {
        "mode": default_profile["mode"],
        "etalons": default_profile["etalons"],
        "levels": DEFAULT_CONFIG["levels"],
        "thresholds": DEFAULT_CONFIG["thresholds"]
    }

CONFIG = load_config()
PROFILE = os.getenv("PROFILE", "default")
MODE = CONFIG.get("mode", "luca")
RULE = RULES.get(MODE, RULES["luca"])

CORE_ETALON_TEXTS = {e["sign"]: e["text"] for e in CONFIG.get("etalons", DEFAULT_CONFIG["profiles"]["default"]["etalons"])}
CORE_SIGNS = set(CORE_ETALON_TEXTS.keys())
LEVEL_MAP = CONFIG.get("levels", DEFAULT_CONFIG["levels"])
SIGN_SPREAD_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("sign_spread", 0.05)
CONFIDENT_COSINE_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("confident_cosine", 0.75)
PATTERN_SECOND_SIGN_THRESHOLD = float(CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("pattern_second_sign_threshold", 30.0))
SEMANTIC_BRIDGE_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("semantic_bridge_threshold", 0.55)


# ============================================================================
# Environment & Paths
# ============================================================================

OBSIDIAN_ROOT = os.getenv("OBSIDIAN_ROOT", "./obsidian")
DATABASE_NAME = "obsidian_kb.db"

EXCLUDED_DIRS = {
    "templates",
    "plugins",
    "memory",
    ".git",
    ".obsidian"
}

LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "")

EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "openai").lower()
EMBED_ENABLED = os.getenv("EMBED_ENABLED", "true").lower() == "true"
EMBED_MODEL = os.getenv("EMBED_MODEL", "")
EMBED_API_URL = os.getenv("EMBED_API_URL", "http://127.0.0.1:1234/v1")
EMBED_API_KEY = os.getenv("EMBED_API_KEY", "")
EMBED_MAX_CHARS = 2000


# ============================================================================
# Logging & Cache
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger("nouz")

embed_cache: Dict[str, List[float]] = {}


# ============================================================================
# Utilities
# ============================================================================

def _safe_path(root: str, rel: str) -> Optional[Path]:
    root_path = Path(root).resolve()
    full = (root_path / rel).resolve()
    if root_path in full.parents or full == root_path:
        return full
    return None

def _serialize(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


# ============================================================================
# LLM & Embeddings
# ============================================================================

async def _call_llm(prompt: str) -> str:
    try:
        url = f"{LLM_API_URL}/chat/completions"
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 500,
        }
        if LLM_MODEL:
            payload["model"] = LLM_MODEL
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"LLM unavailable: {e}")
        return ""

async def _extract_tags(content: str) -> List[str]:
    if not content or not LLM_MODEL:
        return []
    prompt = f"Extract 3-5 keywords from this text. Return them as a comma-separated list without hashtags or numbers.\n\nText: {content[:2000]}"
    result = await _call_llm(prompt)
    if not result:
        return []
    
    tags = [c.strip().lower().lstrip('#') for c in result.replace("\n", ",").split(",") if c.strip()]
    clean_tags = []
    for c in tags:
        c = re.sub(r'^(here|keywords|tags|terms|words).*?:', '', c).strip().lstrip('#')
        if c and 2 < len(c) < 50:
            clean_tags.append(c)
    return list(set(clean_tags))[:5]

async def _get_embedding(text: str) -> List[float]:
    if not EMBED_ENABLED:
        return []
    cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if cache_key in embed_cache:
        return embed_cache[cache_key]
    
    headers = {}
    if EMBED_API_KEY:
        headers["Authorization"] = f"Bearer {EMBED_API_KEY}"
    
    try:
        if EMBED_PROVIDER == "gigachat":
            url = f"{EMBED_API_URL}/embeddings"
            payload = {"input": text, "model": EMBED_MODEL or "EmbeddingsGigaR"}
        elif EMBED_PROVIDER == "ollama":
            url = f"{EMBED_API_URL}/api/embeddings"
            payload = {"model": EMBED_MODEL or "nomic-embed-text", "prompt": text}
        else:
            url = f"{EMBED_API_URL}/embeddings"
            payload = {"input": text, "model": EMBED_MODEL} if EMBED_MODEL else {"input": text}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                
                if EMBED_PROVIDER == "ollama":
                    vec = data.get("embedding", [])
                else:
                    vec = data["data"][0]["embedding"]
                
                embed_cache[cache_key] = vec
                if len(embed_cache) > 500:
                    embed_cache.pop(next(iter(embed_cache)))
                return vec
    except Exception as e:
        logger.warning(f"Embeddings unavailable ({EMBED_PROVIDER}): {e}")
        return []

def _cosine(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(b * b for b in v2) ** 0.5
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


# ============================================================================
# Files & YAML Processing
# ============================================================================

async def read_file_with_metadata(file_path: Path) -> Dict[str, Any]:
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            raw = await f.read()
        try:
            post = frontmatter.loads(raw)
            meta = {k: _serialize(v) for k, v in post.metadata.items()}
            meta['content'] = post.content
        except Exception as fm_err:
            logger.warning(f"frontmatter parse error for {file_path.name}, using fallback: {fm_err}")
            meta = {"path": str(file_path), "content": raw, "frontmatter_error": str(fm_err)}
        meta['path'] = str(file_path)
        return meta
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return {"path": str(file_path), "content": "", "error": str(e)}

def _dump_metadata(metadata: Dict[str, Any]) -> str:
    KEY_ORDER = ['type', 'level', 'sign', 'status', 'tags', 'parents', 'parents_meta']
    ordered = {k: metadata[k] for k in KEY_ORDER if k in metadata}
    ordered.update({k: v for k, v in metadata.items() if k not in KEY_ORDER})

    def _yaml_str(s: str) -> str:
        if any(c in s for c in ':#{}[]|>&*!,\'\"\\'):
            escaped = s.replace("'", "''")
            return f"'{escaped}'"
        return s

    lines = []
    for k, v in ordered.items():
        if k == 'parents' and isinstance(v, list):
            lines.append('parents:')
            for item in v:
                if isinstance(item, str):
                    lines.append(f'- {_yaml_str(item)}')
                elif isinstance(item, dict):
                    entity = item.get('entity', '')
                    lines.append(f'- {_yaml_str(entity)}')
                else:
                    lines.append(f'- {_yaml_str(str(item))}')
        elif k == 'parents_meta' and isinstance(v, list):
            lines.append('parents_meta:')
            for item in v:
                if isinstance(item, dict):
                    first = True
                    for ik, iv in item.items():
                        prefix = '- ' if first else '  '
                        lines.append(f'{prefix}{ik}: {_yaml_str(str(iv))}')
                        first = False
                else:
                    lines.append(f'- {_yaml_str(str(item))}')
        elif isinstance(v, list):
            lines.append(f'{k}:')
            for item in v:
                lines.append(f'- {_yaml_str(str(item)) if isinstance(item, str) else item}')
        elif isinstance(v, str):
            lines.append(f'{k}: {_yaml_str(v)}')
        else:
            lines.append(f'{k}: {v}')
    return '\n'.join(lines)

def _sync_parents_fields(metadata: Dict[str, Any]) -> Dict[str, Any]:
    meta = dict(metadata)
    parents_raw = meta.get('parents', [])
    parents_meta = meta.get('parents_meta', [])

    parents_are_objects = parents_raw and all(isinstance(p, dict) for p in parents_raw)
    if parents_are_objects and not parents_meta:
        parents_meta = parents_raw
        meta['parents_meta'] = parents_meta

    if parents_meta:
        wiki_links = []
        for p in parents_meta:
            if isinstance(p, dict):
                entity = p.get('entity', '')
                if entity:
                    wiki_links.append(entity)
        meta['parents'] = wiki_links
    elif not parents_raw:
        meta['parents'] = []

    return meta

async def write_file_with_metadata(file_path: Path, content: str, metadata: Dict[str, Any], db_path: str = "") -> tuple[bool, str]:
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        synced = _sync_parents_fields(metadata)
        
        if db_path and synced.get("parents_meta"):
            rel_path = str(file_path.relative_to(Path(OBSIDIAN_ROOT))) if file_path.is_absolute() else str(file_path)
            for p in synced["parents_meta"]:
                if isinstance(p, dict):
                    parent_entity = p.get("entity", "")
                    if parent_entity:
                        parent_path = await _resolve_entity_path(db_path, parent_entity)
                        if parent_path:
                            has_cycle = await _check_cycle_exists(db_path, parent_path, rel_path)
                            if has_cycle:
                                logger.warning(f"Cycle detected: {parent_entity} -> {rel_path} (skipped)")
                                return False, "cycle_detected"
        
        yaml_str = _dump_metadata(synced)
        output = f"---\n{yaml_str}\n---\n{content}"
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(output)
        
        if db_path:
            await _index_file(db_path, file_path, {**synced, "content": content})
        
        return True, ""
    except Exception as e:
        logger.error(f"Error writing to {file_path}: {e}")
        return False, str(e)


# ============================================================================
# SQLite Indexing
# ============================================================================

async def init_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                type TEXT,
                sign TEXT,
                sign_manual TEXT,
                sign_auto TEXT,
                sign_source TEXT,
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
        ''')
        
        if RULE["reference_vectors"]:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS reference_vectors (
                    sign TEXT PRIMARY KEY,
                    etalon_text TEXT,
                    embedding TEXT,
                    updated TIMESTAMP
                );
            ''')
        
        await db.commit()

def _get_parents_meta(meta: Dict[str, Any]) -> List[Dict]:
    parents_meta = meta.get('parents_meta', [])
    if parents_meta and isinstance(parents_meta, list):
        result = []
        for p in parents_meta:
            if isinstance(p, dict):
                result.append(p)
            elif isinstance(p, str):
                entity = p.strip()
                if entity.startswith('[[') and entity.endswith(']]'):
                    entity = entity[2:-2]
                if entity:
                    result.append({'entity': entity, 'link_type': 'hierarchy'})
        if result:
            return result
    
    parents_raw = meta.get('parents', [])
    if parents_raw and isinstance(parents_raw, list):
        result = []
        for p in parents_raw:
            if isinstance(p, dict):
                result.append(p)
            elif isinstance(p, str):
                entity = p.strip()
                if entity.startswith('[[') and entity.endswith(']]'):
                    entity = entity[2:-2]
                if entity:
                    result.append({'entity': entity, 'link_type': 'hierarchy'})
        return result
    
    return []

def _check_parents_exist(meta: Dict[str, Any]) -> List[str]:
    parents = _get_parents_meta(meta)
    if not parents:
        return []
    root = Path(OBSIDIAN_ROOT)
    missing = []
    for p in parents:
        entity = p.get('entity', '') if isinstance(p, dict) else str(p)
        if not entity:
            continue
        found = False
        for _ in root.rglob(entity + ".md"):
            found = True
            break
        if not found:
            missing.append(entity)
    return missing

async def _find_orphaned_links(db_path: str) -> List[Dict[str, str]]:
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
                "link_type": link_type
            })
    return orphans

async def _index_file(db_path: str, file_path: Path, meta: Dict[str, Any]):
    parents_obj = _get_parents_meta(meta)
    level = meta.get('level', 0)
    
    yaml_sign = str(meta.get('sign', '')).strip() if meta.get('sign') else ""
    
    resolved_parents = []
    for p in parents_obj:
        if isinstance(p, dict):
            parent_entity = p.get('entity', '')
            link_type = p.get('link_type', 'hierarchy')
            if parent_entity:
                parent_path = await _resolve_entity_path(db_path, parent_entity)
                if parent_path:
                    resolved_parents.append((parent_path, link_type))
                else:
                    resolved_parents.append((parent_entity, link_type))
    
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO files
               (path, type, sign, sign_manual, sign_auto, sign_source, level, status, content, updated, tags, core_mix)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                str(file_path),
                meta.get('type', ''),
                meta.get('sign', ''),
                yaml_sign if yaml_sign else None,
                None,
                None,
                level,
                meta.get('status', 'active'),
                meta.get('content', '')[:2000],
                datetime.now().isoformat(),
                json.dumps(meta.get('tags', meta.get('concepts', [])), ensure_ascii=False),
                json.dumps(meta.get('core_mix', {}), ensure_ascii=False) if meta.get('core_mix') else None,
            )
        )
        
        await db.execute('DELETE FROM links WHERE child_path = ?', (str(file_path),))
        
        for parent_path, link_type in resolved_parents:
            await db.execute(
                'INSERT OR REPLACE INTO links (parent_path, child_path, link_type) VALUES (?, ?, ?)',
                (parent_path, str(file_path), link_type)
            )
        
        await db.commit()

async def _aggregate_core_mix(db_path: str, parent_path: str, child_level: Optional[int] = None) -> Optional[Dict[str, float]]:
    if not RULE["core_mix"]:
        return None
    
    mixes = []
    
    async with aiosqlite.connect(db_path) as db:
        if child_level is not None and RULE["level_strict"]:
            async with db.execute(
                'SELECT f.core_mix FROM files f '
                'JOIN links l ON f.path = l.child_path '
                'WHERE l.parent_path = ? AND l.link_type = "hierarchy" AND f.level = ? AND f.core_mix IS NOT NULL',
                (parent_path, child_level)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                'SELECT f.core_mix FROM files f '
                'JOIN links l ON f.path = l.child_path '
                'WHERE l.parent_path = ? AND l.link_type = "hierarchy" AND f.core_mix IS NOT NULL',
                (parent_path,)
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
    for m in mixes:
        all_keys.update(m.keys())
    result: Dict[str, float] = {}
    for key in all_keys:
        vals = [m.get(key, 0.0) for m in mixes]
        result[key] = round(sum(vals) / len(vals), 1)
    return result


async def _save_embedding(db_path: str, file_path: str, vec: List[float]):
    try:
        mtime = Path(file_path).stat().st_mtime
    except Exception:
        mtime = None
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'INSERT OR REPLACE INTO embeddings (path, embedding, updated, file_mtime) VALUES (?,?,?,?)',
            (file_path, json.dumps(vec), datetime.now().isoformat(), mtime)
        )
        await db.commit()

async def _embedding_is_fresh(db_path: str, file_path: str) -> bool:
    try:
        current_mtime = Path(file_path).stat().st_mtime
    except Exception:
        return False
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT file_mtime FROM embeddings WHERE path=?', (file_path,)
        ) as cur:
            row = await cur.fetchone()
    if not row or row[0] is None:
        return False
    return abs(float(row[0]) - current_mtime) < 1.0

async def _get_db_children(db_path: str, parent_path: str, _visited: Optional[set] = None) -> List[str]:
    if _visited is None:
        _visited = set()
    if parent_path in _visited:
        return []
    _visited.add(parent_path)
    
    children = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT child_path FROM links WHERE parent_path = ? AND link_type = "hierarchy"',
            (parent_path,)
        ) as cur:
            rows = await cur.fetchall()
    for (child_path,) in rows:
        children.append(child_path)
        children.extend(await _get_db_children(db_path, child_path, _visited))
    return children

async def _has_hierarchy_children(db_path: str, file_path: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT 1 FROM links WHERE parent_path = ? AND link_type = "hierarchy" LIMIT 1',
            (file_path,)
        ) as cur:
            row = await cur.fetchone()
    return row is not None

async def _check_cycle_exists(db_path: str, new_parent: str, new_child: str) -> bool:
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
                (current,)
            ) as cur:
                rows = await cur.fetchall()
        for (parent_path,) in rows:
            if parent_path:
                stack.append(parent_path)
    return False

async def _get_db_parents(db_path: str, file_path: str) -> List[Dict]:
    parents = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT parent_path, link_type FROM links WHERE child_path = ?',
            (file_path,)
        ) as cur:
            rows = await cur.fetchall()
    for parent_path, link_type in rows:
        parents.append({
            "entity": Path(parent_path).stem,
            "link_type": link_type
        })
    return parents


# ============================================================================
# Semantic Functions (Prizma/Sloi only)
# ============================================================================

def _signs_share_core(sign_a: str, sign_b: str) -> bool:
    if not sign_a or not sign_b:
        return False
    for ch in sign_a:
        if ch in CORE_SIGNS and ch in sign_b:
            return True
    return False

async def _find_semantic_bridges(
    db_path: str, own_path: str, own_sign: str, own_vec: List[float],
    own_sign_source: str = "auto"
) -> List[Dict]:
    if not RULE["semantic_bridges"] or not own_vec:
        return []

    # If own sign is weak_auto, we don't treat it as a firm domain boundary.
    # Bridges to notes sharing this sign's core are still proposed — because
    # the domain identity of this note is uncertain and cross-domain links may
    # help the user decide whether to confirm the sign or leave it open.
    sign_for_blocking = own_sign if own_sign_source != "weak_auto" else ""

    bridges = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT f.path, f.sign, f.sign_source, e.embedding "
            "FROM files f JOIN embeddings e ON f.path = e.path "
            "WHERE e.embedding IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()

    for path, other_sign, other_sign_source, emb_json in rows:
        if path == own_path:
            continue
        # Block bridge only if BOTH signs are confident (manual or auto).
        # If either side is weak_auto — bridge is still proposed.
        other_sign_for_blocking = (other_sign or "") if other_sign_source != "weak_auto" else ""
        if _signs_share_core(sign_for_blocking, other_sign_for_blocking):
            continue
        try:
            other_vec = json.loads(emb_json)
            sim = _cosine(own_vec, other_vec)
        except Exception:
            continue
        if sim >= SEMANTIC_BRIDGE_THRESHOLD:
            bridges.append({
                "entity": Path(path).stem,
                "link_type": "semantic",
                "strength": round(sim, 3),
                "reason": f"cosine={sim:.2f}, signs={own_sign}↔{other_sign}",
            })

    bridges.sort(key=lambda x: -x["strength"])
    return bridges[:10]

async def _load_reference_vectors(db_path: str) -> Dict[str, List[float]]:
    if not RULE["reference_vectors"]:
        return {}
    
    etalons = {}
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute('SELECT sign, embedding FROM reference_vectors') as cur:
                rows = await cur.fetchall()
        for sign, emb_json in rows:
            try:
                etalons[sign] = json.loads(emb_json)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to load reference_vectors: {e}")
    return etalons

async def _save_core_etalon(db_path: str, sign: str, text: str, vec: List[float]):
    if not RULE["reference_vectors"]:
        return
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'INSERT OR REPLACE INTO reference_vectors (sign, etalon_text, embedding, updated) VALUES (?,?,?,?)',
            (sign, text[:2000], json.dumps(vec), datetime.now().isoformat())
        )
        await db.commit()

async def _calibrate_reference_vectors(db_path: str) -> Dict[str, Any]:
    if not RULE["reference_vectors"]:
        return {"error": "Reference vectors not available in 'luca' mode"}
    
    results = {}
    for sign, text in CORE_ETALON_TEXTS.items():
        vec = await _get_embedding(text)
        if not vec:
            results[sign] = {"status": "error", "reason": "embedding failed"}
            continue
        
        await _save_core_etalon(db_path, sign, text, vec)
        results[sign] = {"status": "ok", "dim": len(vec)}
    
    etalons = await _load_reference_vectors(db_path)
    signs = sorted(etalons.keys())
    pairs = {}
    for i, s1 in enumerate(signs):
        for s2 in signs[i+1:]:
            sim = _cosine(etalons[s1], etalons[s2])
            pairs[f"{s1}-{s2}"] = round(sim, 4)
    
    return {"calibrated": results, "pairwise_cosine": pairs}

def _get_sign_from_file(p: Path) -> str:
    try:
        raw = p.read_text(encoding="utf-8")
        if raw.startswith("---"):
            end = raw.find("\n---\n", 4)
            if end > 0:
                front = yaml.safe_load(raw[4:end])
                if front and front.get("sign"):
                    return str(front["sign"])
    except Exception:
        pass
    return ""

async def _resolve_entity_path(db_path: str, entity_name: str) -> Optional[str]:
    # Try both separators for cross-platform compatibility
    suffix_fwd = f'/{entity_name}.md'
    suffix_bck = f'\\{entity_name}.md'
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT path FROM files WHERE path LIKE ? OR path LIKE ?',
            (f'%{suffix_fwd}', f'%{suffix_bck}')
        ) as cur:
            row = await cur.fetchone()
    if row:
        return row[0]
    root = Path(OBSIDIAN_ROOT)
    for candidate in root.rglob(entity_name + ".md"):
        return str(candidate)
    return None

def _find_parent_sign(meta: Dict, db_path: str) -> str:
    parents = _get_parents_meta(meta)
    if not parents:
        return ""
    root = Path(OBSIDIAN_ROOT)
    for p in parents:
        entity = p.get("entity", "")
        if not entity:
            continue
        for candidate in root.rglob(entity + ".md"):
            sign = _get_sign_from_file(candidate)
            if sign:
                return sign
    return ""

async def _determine_core_by_embedding(content: str, db_path: str) -> Dict[str, Any]:
    if not RULE["reference_vectors"]:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}
    
    vec = await _get_embedding(content[:EMBED_MAX_CHARS])
    if not vec:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}

    etalons = await _load_reference_vectors(db_path)
    if not etalons:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}

    scores = {sign: _cosine(vec, ev) for sign, ev in etalons.items()}

    min_val = min(scores.values())
    max_val = max(scores.values())
    spread = max_val - min_val

    if spread < SIGN_SPREAD_THRESHOLD:
        return {
            "dominant": None,
            "above_threshold": [],
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "percentages": {k: round(100.0 / len(scores), 1) for k in scores},
            "spread": round(spread, 4),
            "max_cosine": round(max_val, 4),
            "confident": False,
        }

    adjusted = {k: v - min_val for k, v in scores.items()}
    total = sum(adjusted.values())
    percentages = {k: round(v / total * 100, 1) for k, v in adjusted.items()}

    above = sorted(
        [k for k, p in percentages.items() if p >= PATTERN_SECOND_SIGN_THRESHOLD],
        key=lambda k: percentages[k],
        reverse=True
    )
    dominant = above[0] if above else None

    # confident = has dominant AND max_cosine is above absolute threshold.
    # If spread is high (clear winner) but max_cosine is low, the note is
    # relatively closer to one core, but not semantically close to any of them
    # in absolute terms — classification is weak.
    confident = bool(dominant) and (max_val >= CONFIDENT_COSINE_THRESHOLD)

    return {
        "dominant": dominant,
        "above_threshold": above,
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "percentages": percentages,
        "spread": round(spread, 4),
        "max_cosine": round(max_val, 4),
        "confident": confident,
    }

async def suggest_parents(file_path: str, db_path: str, top_n: int = 3) -> Dict[str, Any]:
    full_path = Path(file_path)
    if not full_path.exists():
        return {"error": "File not found", "candidates": []}
    
    raw = full_path.read_text(encoding="utf-8")
    content = ""
    if raw.startswith("---"):
        end = raw.find("\n---\n", 4)
        if end > 0:
            content = raw[end + 5:]
    
    if not content:
        return {"error": "Empty content", "candidates": []}
    
    core_result = await _determine_core_by_embedding(content, db_path)
    dominant_core = core_result.get("dominant")
    own_vec = await _get_embedding(content[:EMBED_MAX_CHARS])
    
    if not own_vec:
        return {
            "error": "Embeddings unavailable.",
            "dominant_core": dominant_core,
            "core_scores": core_result.get("scores", {}),
            "candidates": []
        }
    
    candidates = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            'SELECT f.path, f.type, f.level, f.sign, e.embedding FROM files f '
            'LEFT JOIN embeddings e ON f.path = e.path '
            'WHERE e.embedding IS NOT NULL'
        ) as cur:
            rows = await cur.fetchall()
    
    current_level = 5
    try:
        if raw.startswith("---"):
            end = raw.find("\n---\n", 4)
            if end > 0:
                front = yaml.safe_load(raw[4:end])
                if front and front.get("level"):
                    current_level = int(front["level"])
    except Exception:
        pass
    
    for path, ftype, level, sign, emb_json in rows:
        if path == file_path:
            continue
        try:
            other_vec = json.loads(emb_json)
        except Exception:
            continue
        
        sim = _cosine(own_vec, other_vec)
        entity_core = ""
        for ch in (sign or ""):
            if ch in CORE_SIGNS:
                entity_core = ch
                break
        
        candidates.append({
            "path": path,
            "type": ftype,
            "level": level,
            "sign": sign,
            "core": entity_core or "?",
            "same_core": entity_core == dominant_core if dominant_core else False,
            "score": round(sim, 3)
        })
    
    candidates.sort(key=lambda x: (
        not x.get("same_core", False),
        -x["score"]
    ))
    top_candidates = candidates[:top_n]
    
    for c in top_candidates:
        c["recommended_link_type"] = "hierarchy"
    
    return {
        "dominant_core": dominant_core,
        "core_scores": core_result.get("scores", {}),
        "spread": core_result.get("spread", 0),
        "candidates": top_candidates
    }

async def _determine_sign_smart(
    content: str,
    meta: Dict,
    db_path: str
) -> Dict[str, str]:
    if not RULE["has_sign_auto"]:
        return {
            "actual_sign": str(meta.get("sign", "")).strip() if meta.get("sign") else "",
            "sign_auto": "",
            "source": "manual" if meta.get("sign") else "none"
        }
    
    sign_manual = str(meta.get("sign", "")).strip() if meta.get("sign") else ""
    
    core_result = await _determine_core_by_embedding(content, db_path)
    above = core_result.get("above_threshold", [])
    sign_auto = "".join(above) if above else (list(CORE_SIGNS)[0] if CORE_SIGNS else "")
    confident = core_result.get("confident", False)
    
    if sign_manual:
        return {
            "actual_sign": sign_manual,
            "sign_auto": sign_auto,
            "source": "manual",
            "confident": True,  # manual sign is always treated as confident
        }
    
    # "auto" = spread clear + max_cosine above threshold → sign is reliable
    # "weak_auto" = spread shows a winner but max_cosine is low → sign is a
    #   relative best-guess. Semantic bridges to this sign's core are NOT blocked,
    #   because the domain identity is uncertain. User should either confirm the
    #   sign manually or let bridges propose cross-domain links.
    source = "auto" if confident else "weak_auto"

    return {
        "actual_sign": sign_auto,
        "sign_auto": sign_auto,
        "source": source,
        "confident": confident,
    }


# ============================================================================
# Hierarchy Check (Sloi strict mode)
# ============================================================================

def _get_level(type_str: str) -> int:
    return LEVEL_MAP.get(type_str, 0)

def _check_hierarchy_strict(entity_type: str, parents: List[Dict]) -> List[Dict]:
    errors = []
    entity_level = _get_level(entity_type)
    for p in parents:
        p_type = p.get("type", "")
        p_level = _get_level(p_type) if p_type else None
        if p_level is None:
            continue
        if p_level >= entity_level:
            errors.append({
                "type": "level_error",
                "entity": p.get("entity", ""),
                "message": f"Parent level {p_level} >= Child level {entity_level} — reversed hierarchy"
            })
        elif entity_level - p_level > 1:
            errors.append({
                "type": "skip_error",
                "entity": p.get("entity", ""),
                "message": f"Skipped level: {p_level} -> {entity_level} (must be exactly one level below)"
            })
    return errors


# ============================================================================
# Metadata Suggestions
# ============================================================================

def _determine_type(content: str, meta: Dict) -> str:
    return str(meta.get('type', 'entity')).strip().lower() or 'entity'

def _check_hierarchy(entity_type: str, parents: List[Dict]) -> List[Dict]:
    return RULE["hierarchy_check"](entity_type, parents)

async def _suggest_metadata_impl(
    content: str, context: Dict, db_path: str, file_path: str = ""
) -> Dict[str, Any]:
    meta = context or {}
    type_ = _determine_type(content, meta)
    parents_obj = _get_parents_meta(meta)

    tags = meta.get('tags', [])
    if not tags and content:
        tags = await _extract_tags(content)

    semantic_bridges = []
    vec = []
    if file_path and RULE["reference_vectors"]:
        if not await _embedding_is_fresh(db_path, file_path):
            vec = await _get_embedding(content)
            if vec:
                await _save_embedding(db_path, file_path, vec)
        else:
            async with aiosqlite.connect(db_path) as _db:
                async with _db.execute("SELECT embedding FROM embeddings WHERE path=?", (str(file_path),)) as _cur:
                    _row = await _cur.fetchone()
            vec = json.loads(_row[0]) if _row and _row[0] else []
    elif file_path and RULE["reference_vectors"]:
        vec = await _get_embedding(content)
    else:
        vec = []

    sign_result = await _determine_sign_smart(content, meta, db_path)
    actual_sign = sign_result["actual_sign"]
    sign_auto = sign_result["sign_auto"]
    sign_source = sign_result["source"]

    if vec and file_path and RULE["semantic_bridges"]:
        semantic_bridges = await _find_semantic_bridges(
            db_path, str(file_path), actual_sign, vec, own_sign_source=sign_source
        )

    errors = _check_hierarchy(type_, parents_obj)
    
    if parents_obj and file_path:
        for p in parents_obj:
            if isinstance(p, dict) and p.get('link_type', 'hierarchy') == 'hierarchy':
                parent_entity = p.get('entity', '')
                if parent_entity:
                    parent_path = await _resolve_entity_path(db_path, parent_entity)
                    if parent_path:
                        has_cycle = await _check_cycle_exists(db_path, parent_path, str(file_path))
                        if has_cycle:
                            errors.append({
                                "type": "cycle_error",
                                "entity": parent_entity,
                                "message": f"Creating parent link to '{parent_entity}' would create a cycle in the graph"
                            })

    result = {
        "type": type_,
        "sign": actual_sign,
        "sign_manual": meta.get("sign", ""),
        "sign_auto": sign_auto,
        "sign_source": sign_source,
        "status": meta.get("status", "draft"),
        "tags": tags,
        "parents": meta.get("parents", []),
        "parents_meta": parents_obj,
        "errors": errors,
        "semantic_bridges": [
            {**b, "proposed": True} for b in semantic_bridges
        ],
    }

    if content and RULE["reference_vectors"]:
        core_result = await _determine_core_by_embedding(content, db_path)
        if core_result.get("percentages"):
            result["core_percentages"] = core_result["percentages"]
        result["max_cosine"] = core_result.get("max_cosine", 0.0)
        result["confident"] = sign_result.get("confident", False)

    warnings = []
    metrics = {}
    
    if file_path and RULE["core_mix"]:
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    'SELECT core_mix FROM files WHERE path = ?', (file_path,)
                ) as cur:
                    row = await cur.fetchone()
            if row and row[0]:
                stored_mix = json.loads(row[0])
                if stored_mix:
                    leading_core = max(stored_mix, key=lambda k: stored_mix[k])
                    own_sign_cores = [ch for ch in actual_sign if ch in CORE_SIGNS]
                    if own_sign_cores and leading_core != own_sign_cores[0]:
                        pct = stored_mix.get(leading_core, 0)
                        entity_name = meta.get("type", "entity")
                        warnings.append({
                            "type": "core_drift",
                            "message": f"{entity_name} sign={actual_sign!r} (Intent), но содержимое преимущественно {leading_core} ({pct}%) — расхождение замысла и реальности."
                        })
        except Exception:
            pass

    if meta.get("sign") and RULE["has_sign_auto"]:
        metrics["drift_manual_vs_auto"] = (str(meta.get("sign")) != sign_auto)
    
    if file_path and RULE["core_mix"]:
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    'SELECT core_mix FROM files WHERE path = ?', (file_path,)
                ) as cur:
                    row = await cur.fetchone()
            if row and row[0]:
                stored_mix = json.loads(row[0])
                if stored_mix:
                    leading_core = max(stored_mix, key=lambda k: stored_mix[k])
                    auto_cores = [ch for ch in sign_auto if ch in CORE_SIGNS]
                    metrics["drift_auto_vs_core"] = (auto_cores and leading_core != auto_cores[0])
                else:
                    metrics["drift_auto_vs_core"] = False
            else:
                metrics["drift_auto_vs_core"] = False
        except Exception:
            pass
    
    if metrics:
        result["metrics"] = metrics

    if warnings:
        result["warnings"] = warnings

    return result


# ============================================================================
# Compact Entity Formatting
# ============================================================================

def _resolve_entity_meta(entity: str) -> Dict[str, str]:
    root = Path(OBSIDIAN_ROOT)
    for candidate in root.rglob(entity + ".md"):
        try:
            raw = candidate.read_text(encoding="utf-8")
            if raw.startswith("---"):
                end = raw.find("\n---\n", 4)
                m = yaml.safe_load(raw[4:end]) or {}
                return {
                    "sign": str(m.get("sign", "")),
                    "type": str(m.get("type", "")).strip().lower(),
                    "level": str(m.get("level", "")),
                }
        except Exception:
            pass

    signs = "".join(ch for ch in entity if ch in CORE_SIGNS)
    return {"sign": signs, "type": "", "level": ""}

async def format_entity_compact(meta: Dict[str, Any], db_path: str = "") -> str:
    children_signs = []
    if db_path:
        file_path = meta.get("path", "")
        if file_path:
            db_children = await _get_db_children(db_path, file_path)
            for cp in db_children:
                em = _resolve_entity_meta(Path(cp).stem)
                if em.get("sign"):
                    children_signs.append(em["sign"])
                    
    children_str = f"({', '.join(children_signs)})" if children_signs else "()"
    
    own_sign = str(meta.get("sign", "")).strip()
    entity_str = f"[{own_sign}]"
    
    parents_signs = []
    yaml_parents = _get_parents_meta(meta)
    if yaml_parents:
        for p in yaml_parents:
            if isinstance(p, dict):
                p_entity = p.get("entity", "")
                pm = _resolve_entity_meta(p_entity)
                if pm.get("sign"):
                    parents_signs.append(pm["sign"])
                    
    parents_str = f"{{{', '.join(parents_signs)}}}" if parents_signs else "{}"
    
    return f"{children_str}{entity_str}{parents_str}"


# ============================================================================
# Recalc Functions
# ============================================================================

async def _recalc_signs(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    if not RULE["has_sign_auto"]:
        return {
            "error": f"This tool is not available in '{MODE}' mode. Use 'prizma' or 'sloi' mode for semantic classification."
        }
    
    updated = 0
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('SELECT path, content FROM files') as cur:
            rows = await cur.fetchall()
    
    updates = []
    for path, content in rows:
        if not content:
            continue
        sign_result = await _determine_sign_smart(content, {"sign": ""}, db_path)
        actual_sign = sign_result["actual_sign"]
        source = sign_result["source"]
        updates.append((actual_sign, source, path))
        updated += 1
    
    if not dry_run and updates:
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                'UPDATE files SET sign_auto = ?, sign_source = ? WHERE path = ?',
                updates
            )
            await db.commit()
    
    return {"updated": updated, "dry_run": dry_run}

async def _recalc_core_mix(db_path: str) -> Dict[str, Any]:
    if not RULE["core_mix"]:
        return {"error": f"This tool is not available in '{MODE}' mode."}
    
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('SELECT path, level FROM files ORDER BY level DESC') as cur:
            rows = await cur.fetchall()
    
    updates = []
    for path, level in rows:
        if RULE["level_strict"]:
            child_level = (level or 0) - 1 if level else None
        else:
            child_level = None
        core_mix = await _aggregate_core_mix(db_path, path, child_level)
        if core_mix:
            updates.append((json.dumps(core_mix), path))
    
    if updates:
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                'UPDATE files SET core_mix = ? WHERE path = ?',
                updates
            )
            await db.commit()
    
    return {"updated": len(updates)}


# ============================================================================
# MCP Server
# ============================================================================

async def _index_all_files(db_path: str, with_embeddings: bool = False) -> Dict[str, Any]:
    root = Path(OBSIDIAN_ROOT)
    total = 0
    embedded = 0
    errors = 0
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith('.') or part in EXCLUDED_DIRS for part in rel_parts):
            continue
        try:
            data = await read_file_with_metadata(p)
            await _index_file(db_path, p, data)
            total += 1
            if with_embeddings and data.get('content') and RULE["reference_vectors"]:
                if not await _embedding_is_fresh(db_path, str(p)):
                    vec = await _get_embedding(data['content'][:EMBED_MAX_CHARS])
                    if vec:
                        await _save_embedding(db_path, str(p), vec)
                        embedded += 1
                else:
                    embedded += 1 
        except Exception as e:
            logger.warning(f"Indexing error {p}: {e}")
            errors += 1
    orphans = await _find_orphaned_links(db_path)
    return {"indexed": total, "embedded": embedded, "errors": errors, "orphans": orphans}

async def run_server():
    db_path = os.path.join(OBSIDIAN_ROOT, DATABASE_NAME)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_db(db_path)

    logger.info(f"Starting Nouz MCP Server v{VERSION} in '{MODE}' mode...")
    logger.info(f"Mode description: {RULE['description']}")
    
    logger.info("Indexing database on startup...")
    stats = await _index_all_files(db_path, with_embeddings=False)
    if stats.get("orphans"):
        logger.warning(f"Orphaned links found: {len(stats['orphans'])} files with missing parents")
    logger.info(f"Indexed: {stats['indexed']} files, errors: {stats['errors']}")

    if RULE["reference_vectors"]:
        existing_etalons = await _load_reference_vectors(db_path)
        if not existing_etalons:
            logger.info("Core etalons not found - calibrating...")
            cal_result = await _calibrate_reference_vectors(db_path)
            calibrated = cal_result.get("calibrated", {})
            logger.info(f"Calibrated cores: {len(calibrated)}")
        else:
            logger.info(f"Core etalons loaded from DB: {list(existing_etalons.keys())}")
        
        logger.info("Tip: run 'recalc_signs' and 'recalc_core_mix' tools to compute auto-signatures and core_mix.")
    else:
        logger.info("Tip: embeddings and semantic tools are not available in 'luca' mode.")

    server = Server("nouz")
    logger.info(f"Nouz MCP Server v{VERSION} started. OBSIDIAN_ROOT={OBSIDIAN_ROOT}")
    logger.info(f"Mode: {MODE}")
    logger.info(f"Core etalons: {list(CORE_SIGNS)}")
    logger.info(f"Level map: {LEVEL_MAP}")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        tools = [
            types.Tool(
                name="read_file",
                description="Read an Obsidian markdown file and return its YAML frontmatter fields (type, level, sign, parents, tags) "
                            "plus content body as JSON. Also re-indexes the file in the local DB. "
                            "Read-only for the file itself. Use this to inspect any note before making changes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT, e.g. 'notes/my-note.md'"}
                    },
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="write_file",
                description="Write or overwrite an Obsidian markdown file with YAML frontmatter and content body. "
                            "Destructive: replaces file contents entirely. Syncs parents/parents_meta fields automatically "
                            "and checks for DAG cycles before writing. Re-indexes the file in DB after write.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "content": {"type": "string", "description": "Markdown body (without frontmatter delimiters)"},
                        "metadata": {"type": "object", "description": "YAML frontmatter fields: type, level, sign, parents, tags, etc."}
                    },
                    "required": ["path", "content"]
                }
            ),
            types.Tool(
                name="list_files",
                description="List indexed Obsidian files with optional filters. Returns an array of {path, type, level, sign} objects. "
                            "Use level/sign/subfolder to narrow results. Read-only. "
                            "Use this instead of get_children when you need a broad overview rather than hierarchy traversal.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "no_metadata": {"type": "boolean", "description": "If true, include files without YAML frontmatter"},
                        "level": {"type": "integer", "description": "Filter by hierarchy level (1=core, 2=pattern, 3=module, 4=quant, 5=artifact)"},
                        "sign": {"type": "string", "description": "Filter by sign character (e.g. 'T' or 'S')"},
                        "subfolder": {"type": "string", "description": "Restrict search to a subfolder within the vault"}
                    }
                }
            ),
            types.Tool(
                name="get_children",
                description="Get all direct and transitive hierarchy children of a node in the DAG. "
                            "Returns a flat list of relative paths. Read-only. "
                            "Use this to explore what a node contains; use get_parents to see where a node belongs.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"}
                    },
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="get_parents",
                description="Get parent links for a file from the DAG index. Returns an array of {entity, link_type} objects. "
                            "Read-only. Use this to understand a node's position in the hierarchy; "
                            "use get_children for the inverse direction.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"}
                    },
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="suggest_metadata",
                description="Analyze a file's content and suggest metadata: type, sign, tags, semantic bridges, and hierarchy errors. "
                            "Read-only — does not modify the file. Requires embeddings for semantic features (prizma/sloi modes). "
                            "Use this before write_file to validate or improve a note's classification. "
                            "Pass context to override specific frontmatter fields for what-if analysis.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "context": {"type": "object", "description": "Optional frontmatter overrides for what-if analysis (e.g. {sign: 'T'})"}
                    },
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="embed",
                description="Generate a vector embedding for the given text using the configured embedding provider (LM Studio, Ollama, or OpenAI-compatible). "
                            "Returns {embedding: [...], dim: N}. Read-only, no side effects. "
                            "Use this for ad-hoc similarity checks; for batch operations use index_all with with_embeddings=true.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to embed (will be truncated to ~2000 chars)"}
                    },
                    "required": ["text"]
                }
            ),
            types.Tool(
                name="index_all",
                description="Scan all markdown files in the vault and index them into the SQLite database. "
                            "Reports orphaned parent links. Set with_embeddings=true to also compute/update vector embeddings "
                            "(requires embedding provider; skips files whose embeddings are already fresh). "
                            "Run this after adding or reorganizing notes. Safe to re-run — idempotent.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "with_embeddings": {"type": "boolean", "description": "If true, compute embeddings for all files (slower, requires LM Studio/Ollama). Default false."}
                    }
                }
            ),
        ]
        
        if RULE["reference_vectors"]:
            tools.extend([
                types.Tool(
                    name="suggest_parents",
                    description="Find semantically similar notes by vector cosine similarity and suggest them as potential parent links. "
                                "Returns top_n candidates ranked by similarity score, with same-core matches prioritized. "
                                "Read-only — does not modify any files. Requires embeddings (prizma/sloi modes). "
                                "Use this to discover hierarchy links for orphan notes; use suggest_metadata for broader classification.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                            "top_n": {"type": "integer", "description": "Number of candidates to return (default 3)"}
                        },
                        "required": ["path"]
                    }
                ),
            ])
        
        if RULE["has_sign_auto"]:
            tools.extend([
                types.Tool(
                    name="calibrate_cores",
                    description="Recompute reference vector embeddings for all semantic cores defined in config.yaml etalons. "
                                "Writes new vectors to the reference_vectors DB table and reports pairwise cosine similarities. "
                                "Run this once after initial setup, or after changing etalon texts in config.yaml. "
                                "Not available in luca mode.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="recalc_core_mix",
                    description="Recalculate core_mix bottom-up: quants (L4) -> modules (L3) -> patterns (L2). "
                                "Each parent node gets a weighted average of its children's sign distributions. "
                                "Writes updated core_mix to the DB (does not modify YAML files). "
                                "Run after index_all with embeddings or after recalc_signs. Not available in luca mode.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="recalc_signs",
                    description="Reclassify all indexed files by computing their sign_auto from content embeddings vs core etalon vectors. "
                                "Updates sign_auto and sign_source columns in the DB only — does not modify YAML files. "
                                "Use dry_run=true to preview changes without writing. "
                                "Run after calibrate_cores or after adding new notes. Not available in luca mode.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "dry_run": {"type": "boolean", "description": "If true, show what would change without writing to DB (default false)"}
                        }
                    }
                ),
            ])
        
        tools.append(
            types.Tool(
                name="format_entity_compact",
                description="Generate a compact structural formula for a note showing its position in the DAG: "
                            "(children_signs)[own_sign]{parent_signs}. Read-only. "
                            "Available in all modes. Use this to quickly visualize a node's graph neighborhood.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"}
                    },
                    "required": ["path"]
                }
            )
        )
        
        return tools

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        args = arguments or {}
        try:
            if name == "read_file":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]
                data = await read_file_with_metadata(full)
                await _index_file(db_path, full, data)
                missing = _check_parents_exist(data)
                if missing:
                    data['warnings'] = [f"parent_missing: {m}" for m in missing]
                return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]

            elif name == "write_file":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                content = args.get("content", "")
                meta = args.get("metadata", {})
                success, error = await write_file_with_metadata(full, content, meta, db_path)
                if success:
                    return [types.TextContent(type="text", text=json.dumps({"status": "ok", "path": rel}, ensure_ascii=False))]
                else:
                    return [types.TextContent(type="text", text=json.dumps({"status": "error", "reason": error}, ensure_ascii=False))]

            elif name == "list_files":
                root = Path(OBSIDIAN_ROOT)
                files = []
                filter_level = args.get("level")
                filter_sign = args.get("sign")
                subfolder = args.get("subfolder", "")
                
                search_root = root / subfolder if subfolder else root
                
                all_files = []
                for p in search_root.rglob("*.md"):
                    rel_parts = p.relative_to(root).parts
                    if any(part.startswith('.') or part in EXCLUDED_DIRS for part in rel_parts):
                        continue
                    rel = str(p.relative_to(root))
                    all_files.append((str(p), rel))
                
                if not all_files:
                    return [types.TextContent(type="text", text="[]")]
                
                path_list = [pf[0] for pf in all_files]
                placeholders = ','.join(['?' for _ in path_list])
                
                async with aiosqlite.connect(db_path) as db:
                    async with db.execute(
                        f'SELECT path, type, level, sign FROM files WHERE path IN ({placeholders})',
                        path_list
                    ) as cur:
                        rows = await cur.fetchall()
                
                path_to_rel = {pf[0]: pf[1] for pf in all_files}
                db_data = {row[0]: row[1:] for row in rows}
                
                for abs_path, rel in all_files:
                    if abs_path not in db_data:
                        continue
                    ftype, level, sign = db_data[abs_path]
                    if filter_level is not None and level != filter_level:
                        continue
                    if filter_sign and filter_sign not in (sign or ""):
                        continue
                    files.append({
                        "path": rel,
                        "type": ftype,
                        "level": level,
                        "sign": sign
                    })
                
                return [types.TextContent(type="text", text=json.dumps(files, ensure_ascii=False, indent=2))]

            elif name == "get_children":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                children = await _get_db_children(db_path, str(full))
                root = Path(OBSIDIAN_ROOT)
                rel_children = [str(Path(c).relative_to(root)) for c in children]
                return [types.TextContent(type="text", text=json.dumps(rel_children, ensure_ascii=False, indent=2))]

            elif name == "get_parents":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                parents = await _get_db_parents(db_path, str(full))
                return [types.TextContent(type="text", text=json.dumps(parents, ensure_ascii=False, indent=2))]

            elif name == "suggest_metadata":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]
                
                data = await read_file_with_metadata(full)
                content = data.get("content", "")
                # Merge file metadata with user-provided context (context overrides)
                file_meta = {k: v for k, v in data.items() if k != "content"}
                context = args.get("context", {})
                merged_context = {**file_meta, **context} if context else file_meta
                result = await _suggest_metadata_impl(content, merged_context, db_path, str(full))
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "embed":
                text = args.get("text", "")
                vec = await _get_embedding(text)
                if not vec:
                    return [types.TextContent(type="text", text=json.dumps({"error": "Embeddings unavailable"}, ensure_ascii=False))]
                return [types.TextContent(type="text", text=json.dumps({"embedding": vec, "dim": len(vec)}, ensure_ascii=False))]

            elif name == "index_all":
                with_embeddings = args.get("with_embeddings", False)
                result = await _index_all_files(db_path, with_embeddings=with_embeddings)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "suggest_parents":
                if not RULE["reference_vectors"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": f"This tool is not available in '{MODE}' mode. Use 'prizma' or 'sloi' mode for semantic classification."}, ensure_ascii=False))]
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                top_n = args.get("top_n", 3)
                result = await suggest_parents(str(full), db_path, top_n)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "calibrate_cores":
                if not RULE["reference_vectors"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": f"This tool is not available in '{MODE}' mode. Use 'prizma' or 'sloi' mode for semantic classification."}, ensure_ascii=False))]
                result = await _calibrate_reference_vectors(db_path)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "recalc_signs":
                if not RULE["has_sign_auto"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": f"This tool is not available in '{MODE}' mode. Use 'prizma' or 'sloi' mode for semantic classification."}, ensure_ascii=False))]
                dry_run = args.get("dry_run", False)
                result = await _recalc_signs(db_path, dry_run)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "recalc_core_mix":
                if not RULE["core_mix"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": f"This tool is not available in '{MODE}' mode."}, ensure_ascii=False))]
                result = await _recalc_core_mix(db_path)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "format_entity_compact":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]
                
                data = await read_file_with_metadata(full)
                formula = await format_entity_compact(data, db_path)
                return [types.TextContent(type="text", text=json.dumps({"formula": formula}, ensure_ascii=False))]

            else:
                return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False))]

        except Exception as e:
            logger.exception(f"Tool error: {name}")
            return [types.TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run_server())
