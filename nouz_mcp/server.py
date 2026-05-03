#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nouz -- Unified MCP Server for Obsidian. v3.0.0

Three modes:
- luca: Graph-based, level is for display only, no semantic classification
- prizma: Graph-based with semantic bridges and core_mix
- sloi: Strict 5-level hierarchy with semantic classification
"""

VERSION = "3.0.0"

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

DEFAULT_ARTIFACT_SIGNS = [
    {"sign": "β", "name": "Note", "text": "Short note, observation, fragment."},
    {"sign": "δ", "name": "Concept", "text": "Definition, concept, entity description."},
    {"sign": "ζ", "name": "Reference", "text": "External source, documentation, link, citation."},
    {"sign": "σ", "name": "Log", "text": "Session log, chronology, dialogue record."},
    {"sign": "μ", "name": "News", "text": "News item, update, release note."},
    {"sign": "λ", "name": "Hypothesis", "text": "Hypothesis, assumption, speculative idea."},
    {"sign": "🝕", "name": "Specification", "text": "Technical specification, instruction, requirements."},
]

DEFAULT_CONFIG = {
    "mode": "luca",
    "etalons": [],
    "artifact_signs": DEFAULT_ARTIFACT_SIGNS,
    "meta_root": "",
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
        "confident_cosine": 0.6,
        "confident_spread": 60.0,
        "pattern_second_sign_threshold": 30.0,
        "semantic_bridge_threshold": 0.55,
        "parent_link_threshold": 0.55
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

def _apply_profile(config: Dict[str, Any], profile_name: str, source: Path) -> Dict[str, Any]:
    profiles = config.get("profiles", {})
    if profiles and profile_name in profiles:
        profile = profiles[profile_name]
        merged = dict(config)
        merged["mode"] = profile.get("mode", config.get("mode", "luca"))
        merged["etalons"] = profile.get("etalons", config.get("etalons", []))
        logging.info(f"Loaded config from {source}, profile: {profile_name}")
        return merged
    logging.info(f"Loaded config from {source}")
    return config

def load_config() -> Dict[str, Any]:
    base_dir = Path(__file__).parent
    profile_name = os.getenv("PROFILE", "default")

    candidates: List[Path] = []
    if os.getenv("NOUZ_CONFIG"):
        candidates.append(Path(os.environ["NOUZ_CONFIG"]))
    candidates.extend([
        Path.cwd() / "config_local.yaml",
        base_dir / "config_local.yaml",
        Path.cwd() / "config.yaml",
        base_dir / "config.yaml",
    ])

    seen: Set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if not candidate.exists():
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            return _apply_profile(config, profile_name, candidate)
        except Exception as e:
            logging.warning(f"Failed to load config from {candidate}: {e}")

    logging.info("No config.yaml found; using LUCA defaults. Copy config.template.yaml to config.yaml to enable PRIZMA/SLOI.")
    return DEFAULT_CONFIG

CONFIG = load_config()
MODE = CONFIG.get("mode", "luca")
RULE = RULES.get(MODE, RULES["luca"])

CORE_ETALON_TEXTS = {e["sign"]: e["text"] for e in CONFIG.get("etalons", DEFAULT_CONFIG["etalons"])}
CORE_SIGNS = set(CORE_ETALON_TEXTS.keys())
CONFIG_SIGN_CHARS = set(CONFIG.get("sign_chars", ""))

# Artifact signs are labels for heuristic classification, not embedding etalons.
# "artifact_etalons" is accepted as a legacy config key for older local configs.
ARTIFACT_SIGN_LIST = (
    CONFIG.get("artifact_signs")
    or CONFIG.get("artifact_etalons")
    or DEFAULT_CONFIG["artifact_signs"]
)
ARTIFACT_SIGNS = set(e["sign"] for e in ARTIFACT_SIGN_LIST)
ARTIFACT_SIGN_TEXTS = {e["sign"]: e.get("text", "") for e in ARTIFACT_SIGN_LIST}
LEVEL_MAP = CONFIG.get("levels", DEFAULT_CONFIG["levels"])
SIGN_SPREAD_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("sign_spread", 0.05)
CONFIDENT_SPREAD_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("confident_spread", 60.0)

# Meta-root: level 0 anchor, excluded from all semantic processing
META_ROOT_NAME = CONFIG.get("meta_root", "")

def _is_meta_root(file_path) -> bool:
    """True if this file is the meta-root (level 0 anchor). Skip all semantic ops."""
    if not META_ROOT_NAME:
        return False
    stem = Path(str(file_path)).stem
    return stem == META_ROOT_NAME or stem.endswith(META_ROOT_NAME)

# ============================================================================
# Level type mapping
# ============================================================================

LEVEL_TO_TYPE = {
    0: "meta",
    1: "core",
    2: "pattern",
    3: "module",
    4: "quant",
    5: "artifact"
}

def _get_type_by_level(level: int) -> str:
    return LEVEL_TO_TYPE.get(level, "artifact")

PATTERN_SECOND_SIGN_THRESHOLD = float(CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("pattern_second_sign_threshold", 30.0))
SEMANTIC_BRIDGE_THRESHOLD = CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("semantic_bridge_threshold", 0.55)
PARENT_LINK_THRESHOLD = float(CONFIG.get("thresholds", DEFAULT_CONFIG["thresholds"]).get("parent_link_threshold", 0.55))

# ============================================================================
# Environment & Paths
# ============================================================================

OBSIDIAN_ROOT = os.getenv("OBSIDIAN_ROOT", "./obsidian")
DATABASE_NAME = os.getenv("NOUZ_DATABASE_NAME", os.getenv("NOUZ_DB_NAME", "obsidian_kb.db"))
DATABASE_PATH = os.getenv("NOUZ_DATABASE_PATH", os.getenv("NOUZ_DB_PATH", ""))

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

def _default_db_path() -> str:
    """Return the active SQLite cache path for this server instance."""
    if DATABASE_PATH:
        return DATABASE_PATH
    return os.path.join(OBSIDIAN_ROOT, DATABASE_NAME)

def _db_path_to_file(path_str: str) -> Path:
    """Resolve a path stored in SQLite back to a Markdown file path."""
    p = Path(path_str)
    if p.is_absolute() or p.exists():
        return p
    return Path(OBSIDIAN_ROOT) / p

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
        if EMBED_PROVIDER == "ollama":
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


def _mean_center(vecs: Dict[str, List[float]]) -> Dict[str, List[float]]:
    """Subtract the mean vector from all vectors (anisotropy correction).
    
    Transformer embeddings cluster in a narrow cone, inflating all pairwise
    cosine similarities. Subtracting the centroid removes this shared component
    and reveals true semantic distances (Su et al. 2021, WhitenedCSE 2023).
    """
    if len(vecs) < 2:
        return vecs
    dim = len(next(iter(vecs.values())))
    mean = [0.0] * dim
    for v in vecs.values():
        for i in range(dim):
            mean[i] += v[i]
    n = len(vecs)
    mean = [m / n for m in mean]
    return {k: [v[i] - mean[i] for i in range(dim)] for k, v in vecs.items()}


# ============================================================================
# Files & YAML Processing
# ============================================================================

def _parse_frontmatter(raw: str) -> tuple[Dict[str, Any], str]:
    """Parse Markdown YAML frontmatter with python-frontmatter or legacy frontmatter."""
    if not raw.startswith("---"):
        return {}, raw

    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, raw
    close_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            close_idx = idx
            break
    if close_idx is None:
        return {}, raw

    header = "".join(lines[1:close_idx])
    if not re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*\s*:", header):
        return {}, raw

    body = "".join(lines[close_idx + 1:])
    attrs = yaml.safe_load(header) or {}
    if not isinstance(attrs, dict):
        return {}, raw
    return dict(attrs), body

async def read_file_with_metadata(file_path: Path) -> Dict[str, Any]:
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            raw = await f.read()
        try:
            attrs, body = _parse_frontmatter(raw)
            meta = {k: _serialize(v) for k, v in attrs.items()}
            meta['content'] = body
        except Exception as fm_err:
            logger.warning(f"frontmatter parse error for {file_path.name}, using fallback: {fm_err}")
            meta = {"path": str(file_path), "content": raw, "frontmatter_error": str(fm_err)}
        meta['path'] = str(file_path)
        return meta
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return {"path": str(file_path), "content": "", "error": str(e)}

def _clean_content(content: str) -> str:
    content = re.sub(r'\n*---\s*\n## Связи\s*\n.*', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*---\s*\n## Иерархия\s*\(для графа\).*', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*## Связи\s*\n(?:\*\*Родители:\*\*.*?\n)?(?:\*\*Дети:\*\*.*?\n)?', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*## Иерархия(?:\s*\(для графа\))?\s*\n(?:\*\*Parents:\*\*.*?\n)?(?:\*\*Children:\*\*.*?\n)?', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*## Links\s*\n(?:\*\*Parents:\*\*.*?\n)?(?:\*\*Children:\*\*.*?\n)?', '', content, flags=re.DOTALL)
    content = re.sub(r'\n*## Hierarchy\s*\n(?:\*\*Parents:\*\*.*?\n)?(?:\*\*Children:\*\*.*?\n)?', '', content, flags=re.DOTALL)
    content = re.sub(r"^path:\s*['\"].*?['\"]\s*\n?", '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*---\s*\n', '', content)
    return content.strip()


def _split_frontmatter_raw(raw: str) -> tuple[Dict[str, Any], str]:
    """Return frontmatter metadata and body without changing the body text."""
    raw_for_match = raw[1:] if raw.startswith("\ufeff") else raw
    match = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', raw_for_match, re.DOTALL)
    if not match:
        return {}, raw
    try:
        fm = yaml.safe_load(match.group(1)) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, match.group(2)


def _dump_metadata(metadata: Dict[str, Any]) -> str:
    # Whitelist: only these keys are written to YAML.
    # Internal fields (sign_source, sign_auto, color, core_mix, path, content, etc.) are not included.
    YAML_ALLOWED_KEYS = {'type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta'}
    KEY_ORDER = ['type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta']

    def _has_yaml_value(value: Any) -> bool:
        if value is None or value == []:
            return False
        if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
            return False
        return True

    ordered = {
        k: metadata[k]
        for k in KEY_ORDER
        if k in metadata and k in YAML_ALLOWED_KEYS and _has_yaml_value(metadata[k])
    }
    ordered.update({
        k: v
        for k, v in metadata.items()
        if k in YAML_ALLOWED_KEYS and k not in KEY_ORDER and _has_yaml_value(v)
    })
    
    # artifact_sign only makes sense in YAML for L4 (composite sign)
    level_val = metadata.get("level", 5)
    if level_val != 4 and "artifact_sign" in ordered:
        del ordered["artifact_sign"]

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

async def write_file_with_metadata(
    file_path: Path,
    content: str,
    metadata: Dict[str, Any],
    db_path: str = "",
    *,
    clean_content: bool = False,
) -> tuple[bool, str]:
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
        body = _clean_content(content) if clean_content else content
        output = f"---\n{yaml_str}\n---\n{body}"
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(output)
        
        if db_path:
            await _index_file(db_path, file_path, {**synced, "content": body})
        
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
    
    # Migration: add artifact_sign column if it doesn't exist (SQLite < 3.38.0)
    await _migrate_artifact_sign(db_path)

async def _migrate_artifact_sign(db_path: str):
    """Add artifact_sign column to existing databases and populate L5 files."""
    async with aiosqlite.connect(db_path) as db:
        # Check if column exists
        async with db.execute("PRAGMA table_info(files)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        
        if "artifact_sign" not in columns:
            logger.info("Migration: adding artifact_sign column to files table")
            await db.execute("ALTER TABLE files ADD COLUMN artifact_sign TEXT")
            await db.commit()
            
            # Populate artifact_sign for existing L5 files using heuristic
            async with db.execute("SELECT path, content FROM files WHERE level = 5 AND content IS NOT NULL") as cur:
                l5_rows = await cur.fetchall()
            
            if l5_rows:
                updates = []
                for path, content in l5_rows:
                    art_sign = _determine_artifact_sign(content, {})
                    updates.append((art_sign, path))
                
                await db.executemany(
                    "UPDATE files SET artifact_sign = ? WHERE path = ?",
                    updates
                )
                await db.commit()
                logger.info(f"Migration: populated artifact_sign for {len(updates)} L5 files")

def _extract_artifact_sign_from_sign(sign: str) -> str:
    """Extract artifact_sign portion from a composite sign (L4: artifact_sign + core_sign).
    
    Returns the artifact_sign characters (those in ARTIFACT_SIGNS).
    """
    return "".join(ch for ch in sign if ch in ARTIFACT_SIGNS)

def _dedupe_sign_chars(sign: str) -> str:
    """Dedupe sign characters while preserving their original order."""
    result: List[str] = []
    for ch in sign or "":
        if ch not in result:
            result.append(ch)
    return "".join(result)

def _extract_core_sign_from_sign(sign: str) -> str:
    """Extract core_sign portion from a composite sign.
    
    Returns the core_sign characters. If no semantic core signs are configured
    (for example in luca mode), treat every non-artifact sign character as a
    structural/core sign so plain text signs keep user-defined domain characters.
    """
    if CORE_SIGNS:
        return "".join(ch for ch in sign if ch in CORE_SIGNS)
    return "".join(ch for ch in sign if ch not in ARTIFACT_SIGNS)

def _determine_artifact_sign(content: str, meta: Dict) -> str:
    """Determine artifact sign by content structure/heuristics — no embeddings needed.
    
    Artifact signs describe FORMAT/STRUCTURE, not topic. This is intentional:
    a log about physics should be σ (log), not D (domain) — the embedding captures
    the physics topic for semantic bridges, while the sign captures the format.
    
    Priority order: most specific/structured first, least specific (β) as fallback.
    """
    if not content:
        return "β"  # Default: note
    
    text = content.lower()
    
    # 🝕 Спецификация — requirements, must/should, architecture docs
    spec_kw = ['должно быть', 'требования', 'спецификац', 'инструкц', 'архитектурн',
               'техническ задан', 'тз:', 'must be', 'requirements', 'specification']
    if any(kw in text for kw in spec_kw):
        return "🝕"
    
    # σ Лог — chronology, timestamps, session records
    log_kw = ['лог ', 'сессия', 'сначала', 'потом', 'далее,', 'хронолог',
              'что сделали', 'что получилось', 'что не получилось',
              'session log', 'chronology', 'timeline', 'step by step']
    if any(kw in text for kw in log_kw):
        return "σ"
    
    # μ Новость — news, updates, fresh info
    news_kw = ['новость', 'обновлен', 'свеж', 'произошло', 'что нового',
               'стало известно', 'вышло', 'релиз', 'news:', 'update:', 'released']
    if any(kw in text for kw in news_kw):
        return "μ"
    
    # λ Гипотеза — hypothesis, speculation, assumptions
    hyp_kw = ['гипотез', 'предположим', 'может быть', 'возможно,', 'спекуляц',
              'допущен', 'предположен', 'если бы', 'что если', 'hypothesis',
              'speculation', 'what if', 'suppose that']
    if any(kw in text for kw in hyp_kw):
        return "λ"
    
    # ζ Референс — external links, documentation, third-party
    ref_kw = ['http://', 'https://', 'www.', 'документац', 'сторонн', 'ссылк',
              'внешн', 'обзор ', 'каталог', 'reference:', 'documentation']
    if any(kw in text for kw in ref_kw):
        return "ζ"
    
    # δ Понятие — definition, concept, entity description
    concept_kw = ['поняти', 'определен', 'концепт', 'сущност', 'это когда',
                  'это такой', 'это то,', 'границы понятия', 'свойства',
                  'отличия от', 'definition', 'concept:', 'entity']
    if any(kw in text for kw in concept_kw):
        return "δ"
    
    # β Заметка — default: short note, observation, fragment
    return "β"

async def _collect_artifact_sign_from_children(meta: Dict, db_path: str) -> str:
    """Collect artifact_sign from child artifacts (level=5) via links table.
    
    For a quant (L4), the horizontal part of the composite sign comes from
    the artifact(s) it was created from (its children in the graph).
    We look up child files in the links table and extract their artifact_sign.
    
    Returns deduplicated artifact_sign characters (e.g., "σ" or "σλ").
    """
    artifact_signs: List[str] = []
    path = meta.get("path", "")
    if not path or not db_path:
        return ""
    
    async with aiosqlite.connect(db_path) as db:
        # Find children (where this entity is the parent)
        async with db.execute(
            "SELECT child_path FROM links WHERE parent_path = ? AND link_type = 'hierarchy'",
            (path,)
        ) as cur:
            rows = await cur.fetchall()
        
        for row in rows:
            child_path = row[0]
            try:
                child_full = _db_path_to_file(child_path)
                if not child_full.exists():
                    continue
                fm, _ = _split_frontmatter_raw(child_full.read_text(encoding="utf-8"))
                art_sign = str(fm.get("artifact_sign") or fm.get("sign", ""))
                for ch in _extract_artifact_sign_from_sign(art_sign):
                    if ch not in artifact_signs:
                        artifact_signs.append(ch)
            except Exception:
                pass
    
    return "".join(artifact_signs)


async def _find_temporary_anchor(content: str, db_path: str, level: int = 5) -> Optional[str]:
    """Find a domain anchor for a temporary link based on content's core sign.
    
    For L5 artifacts: the artifact sign is format (σ/β/etc), not domain.
    We use embedding to determine the domain, then find the nearest L1/L2 
    entity with that core sign. This gives the artifact a "parking spot" 
    in the hierarchy until it's properly linked.
    
    Returns entity name or None.
    """
    if not RULE["reference_vectors"]:
        return None
    
    try:
        core_result = await _determine_core_by_embedding(content, db_path)
        dominant_core = core_result.get("dominant")
        if not dominant_core:
            return None
        
        candidates = []
        
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                'SELECT path, sign, level FROM files WHERE level IN (1, 2) AND sign IS NOT NULL'
            ) as cur:
                rows = await cur.fetchall()
        
        for p_path, p_sign, p_level in rows:
            if not p_sign:
                continue
            p_cores = [ch for ch in p_sign if ch in CORE_SIGNS]
            if dominant_core in p_cores:
                entity_name = Path(p_path).stem
                candidates.append((entity_name, p_level))
        
        if not candidates:
            return None
        
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    except Exception:
        return None


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
    level = meta.get('level')
    if level == "":
        level = None
    
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
               (path, type, sign, sign_manual, sign_auto, sign_source, artifact_sign, level, status, content, updated, tags, core_mix)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                str(file_path),
                meta.get('type', ''),
                meta.get('sign', ''),
                yaml_sign if yaml_sign else None,
                None,
                None,
                meta.get('artifact_sign', ''),
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

    sign_for_blocking = own_sign if own_sign_source != "weak_auto" else ""

    bridges = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT f.path, f.sign, f.sign_source, f.artifact_sign, e.embedding "
            "FROM files f JOIN embeddings e ON f.path = e.path "
            "WHERE e.embedding IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()

    for path, other_sign, other_sign_source, other_artifact_sign, emb_json in rows:
        if path == own_path:
            continue
        other_sign_for_blocking = (other_sign or "") if other_sign_source != "weak_auto" else ""
        if _signs_share_core(sign_for_blocking, other_sign_for_blocking):
            continue
        try:
            other_vec = json.loads(emb_json)
            sim = _cosine(own_vec, other_vec)
        except Exception:
            continue
        if sim >= SEMANTIC_BRIDGE_THRESHOLD:
            other_display = other_sign or other_artifact_sign or "?"
            bridges.append({
                "entity": Path(path).stem,
                "link_type": "semantic",
                "strength": round(sim, 3),
                "reason": f"cosine={sim:.2f}, signs={own_sign}<->{other_display}",
            })

    bridges.sort(key=lambda x: -x["strength"])
    return bridges[:10]

async def _find_tag_bridges(
    db_path: str, own_path: str, own_tags: List[str], own_sign: str,
    threshold: float = 0.72
) -> List[Dict]:
    """Gray bridges: semantic similarity at the tag level, not full text.

    Algorithm:
    - For each tag of the current note: compute embedding
    - For each other note with a DIFFERENT core: compute embedding of each tag
    - If at least one tag pair gives cosine >= threshold -> gray bridge
    - Finds partial concept overlap, not full-text similarity

    Difference from pink (semantic) bridges:
    - Pink: cosine of full text -- "these notes are about the same thing"
    - Gray: cosine of individual tags -- "these notes share a hidden concept"

    Example: note about thermodynamic entropy and note about technical debt:
    texts are different, but tag "entropy" can be close to tag "disorder"
    in embedding space, so the bridge reveals a shared concept.
    """
    if not RULE["semantic_bridges"] or not own_tags:
        return []

    own_tag_vecs: Dict[str, List[float]] = {}
    for tag in own_tags:
        vec = await _get_embedding(tag)
        if vec:
            own_tag_vecs[tag] = vec

    if not own_tag_vecs:
        return []

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT f.path, f.sign, f.artifact_sign, f.tags FROM files f "
            "WHERE f.tags IS NOT NULL AND f.tags != '[]' AND f.tags != ''"
        ) as cur:
            rows = await cur.fetchall()

    bridges = []
    seen_paths = set()

    for path, other_sign, other_artifact_sign, tags_json in rows:
        if path == own_path or path in seen_paths:
            continue
        other_display = other_sign or other_artifact_sign or ""
        if _signs_share_core(own_sign, other_display):
            continue

        try:
            other_tags = json.loads(tags_json) if isinstance(tags_json, str) else tags_json
            if not other_tags:
                continue
        except Exception:
            continue

        best_pair = None
        best_sim = 0.0
        for own_tag, own_vec in own_tag_vecs.items():
            for other_tag in other_tags:
                other_vec = await _get_embedding(other_tag)
                if not other_vec:
                    continue
                sim = _cosine(own_vec, other_vec)
                if sim >= threshold and sim > best_sim:
                    best_sim = sim
                    best_pair = (own_tag, other_tag)

        if best_pair:
            seen_paths.add(path)
            bridges.append({
                "entity": Path(path).stem,
                "link_type": "tag",
                "strength": round(best_sim, 3),
                "reason": f"tag bridge: '{best_pair[0]}' <-> '{best_pair[1]}' (cosine={best_sim:.2f}), signs={own_sign}<->{other_display}",
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
    centered = _mean_center(etalons)
    signs = sorted(etalons.keys())
    pairs = {}
    centered_pairs = {}
    for i, s1 in enumerate(signs):
        for s2 in signs[i+1:]:
            sim = _cosine(etalons[s1], etalons[s2])
            pairs[f"{s1}-{s2}"] = round(sim, 4)
            csim = _cosine(centered[s1], centered[s2])
            centered_pairs[f"{s1}-{s2}"] = round(csim, 4)
    
    return {"calibrated": results, "pairwise_cosine": pairs, "pairwise_cosine_centered": centered_pairs}

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

async def _determine_core_by_embedding(content: str, db_path: str) -> Dict[str, Any]:
    """Classify content against core etalon vectors using mean-centered cosine similarity.

    Returns spread-normalized percentages and a confidence flag.
    confident=True when the dominant core reaches CONFIDENT_SPREAD_THRESHOLD (default 60%)
    of the normalized spread -- this is more reliable than raw cosine thresholding
    because transformer embedding spaces are anisotropic.
    """
    if not RULE["reference_vectors"]:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}
    
    vec = await _get_embedding(content[:EMBED_MAX_CHARS])
    if not vec:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}

    etalons = await _load_reference_vectors(db_path)
    if not etalons:
        return {"dominant": None, "above_threshold": [], "scores": {}, "percentages": {}, "spread": 0.0, "max_cosine": 0.0, "confident": False}

    centered = _mean_center(etalons)
    centroid = [0.0] * len(vec)
    n = len(centered)
    for cv in centered.values():
        for i in range(len(centroid)):
            centroid[i] += cv[i] / n
    vec_centered = [vec[i] - centroid[i] for i in range(len(vec))]

    scores_raw = {sign: _cosine(vec, ev) for sign, ev in etalons.items()}
    scores = {sign: _cosine(vec_centered, centered[sign]) for sign in centered}

    min_val = min(scores.values())
    max_val = max(scores.values())
    spread = max_val - min_val

    if spread < SIGN_SPREAD_THRESHOLD:
        return {
            "dominant": None,
            "above_threshold": [],
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "scores_raw": {k: round(v, 4) for k, v in scores_raw.items()},
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
    dominant_pct = percentages[dominant] if dominant else 0.0
    confident = bool(dominant) and dominant_pct >= CONFIDENT_SPREAD_THRESHOLD

    return {
        "dominant": dominant,
        "above_threshold": above,
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "scores_raw": {k: round(v, 4) for k, v in scores_raw.items()},
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
    db_path: str,
    level: int = 5
) -> Dict[str, str]:
    """Determine sign(s) based on entity level.
    
    L5 (artifact): artifact_sign by heuristic, no core_sign
    L4 (quant): optional own artifact_sign + core_sign from embedding
    L1-L3 (core/pattern/module): only core_sign from embedding
    """
    if not RULE["has_sign_auto"]:
        return {
            "actual_sign": str(meta.get("sign", "")).strip() if meta.get("sign") else "",
            "sign_auto": "",
            "artifact_sign": "",
            "source": "manual" if meta.get("sign") else "none"
        }
    
    sign_manual = str(meta.get("sign", "")).strip() if meta.get("sign") else ""
    
    if level == 5:
        # Artifact: only artifact_sign (horizontal), no core_sign (vertical)
        artifact_sign = _determine_artifact_sign(content, meta)
        actual_sign = artifact_sign
        
        if sign_manual:
            return {
                "actual_sign": sign_manual,
                "sign_auto": "",
                "artifact_sign": artifact_sign,
                "source": "manual",
                "confident": True,
            }
        
        return {
            "actual_sign": actual_sign,
            "sign_auto": "",
            "artifact_sign": artifact_sign,
            "source": "auto",
            "confident": True,
        }
    
    elif level == 4:
        # Quant: composite sign = own artifact_sign + core_sign.
        # Child artifact inventory belongs in DB aggregation/UI, not in YAML sign.
        artifact_sign = str(meta.get("artifact_sign", "") or "")
        
        # Determine core_sign from embedding
        core_result = await _determine_core_by_embedding(content, db_path)
        above = core_result.get("above_threshold", [])
        core_sign = "".join(above) if above else (list(CORE_SIGNS)[0] if CORE_SIGNS else "")
        confident = core_result.get("confident", False)
        
        composite = artifact_sign + core_sign
        actual_sign = composite if composite else core_sign
        
        if sign_manual:
            return {
                "actual_sign": sign_manual,
                "sign_auto": core_sign,
                "artifact_sign": artifact_sign,
                "source": "manual",
                "confident": True,
            }
        
        source = "auto" if confident else "weak_auto"
        return {
            "actual_sign": actual_sign,
            "sign_auto": core_sign,
            "artifact_sign": artifact_sign,
            "source": source,
            "confident": confident,
        }
    
    else:
        # L1-L3 (core/pattern/module): only core_sign
        core_result = await _determine_core_by_embedding(content, db_path)
        above = core_result.get("above_threshold", [])
        sign_auto = "".join(above) if above else (list(CORE_SIGNS)[0] if CORE_SIGNS else "")
        confident = core_result.get("confident", False)
        
        if sign_manual:
            return {
                "actual_sign": sign_manual,
                "sign_auto": sign_auto,
                "artifact_sign": "",
                "source": "manual",
                "confident": True,
            }
        
        source = "auto" if confident else "weak_auto"
        return {
            "actual_sign": sign_auto,
            "sign_auto": sign_auto,
            "artifact_sign": "",
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
                "message": f"Parent level {p_level} >= Child level {entity_level} -- reversed hierarchy"
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
    if file_path and not meta.get("path"):
        meta["path"] = file_path
    type_ = _determine_type(content, meta)
    parents_obj = _get_parents_meta(meta)

    tags = meta.get('tags', [])
    if not tags and content:
        tags = await _extract_tags(content)

    semantic_bridges = []
    tag_bridges = []
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

    sign_result = await _determine_sign_smart(content, meta, db_path, level=_get_level_from_meta(meta))
    actual_sign = sign_result["actual_sign"]
    sign_auto = sign_result["sign_auto"]
    sign_source = sign_result["source"]
    artifact_sign = sign_result.get("artifact_sign", "")

    if vec and file_path and RULE["semantic_bridges"]:
        semantic_bridges = await _find_semantic_bridges(
            db_path, str(file_path), actual_sign, vec, own_sign_source=sign_source
        )
        if tags:
            tag_bridges = await _find_tag_bridges(db_path, str(file_path), tags, actual_sign)

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
        "artifact_sign": artifact_sign,
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
        "tag_bridges": [
            {**b, "proposed": True} for b in tag_bridges
        ],
    }

    if content and RULE["reference_vectors"]:
        core_result = await _determine_core_by_embedding(content, db_path)
        if core_result.get("percentages"):
            result["core_percentages"] = core_result["percentages"]
        result["max_cosine"] = core_result.get("max_cosine", 0.0)
        result["confident"] = core_result.get("confident", False)

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
                            "message": f"{entity_name} sign={actual_sign!r} (Intent), but content is predominantly {leading_core} ({pct}%) -- drift between intent and reality."
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

def _get_level_from_meta(meta: Dict[str, Any], default: int = 5) -> int:
    """Get level from metadata, safely"""
    level_val = meta.get("level", default)
    if level_val is None or level_val == "":
        return default
    try:
        return int(level_val)
    except (ValueError, TypeError):
        return default

def _resolve_entity_meta(entity: str) -> Dict[str, str]:
    root = Path(OBSIDIAN_ROOT)
    for candidate in root.rglob(entity + ".md"):
        try:
            raw = candidate.read_text(encoding="utf-8")
            if raw.startswith("---"):
                end = raw.find("\n---\n", 4)
                if end > 0:
                    m = yaml.safe_load(raw[4:end]) or {}
                    sign_val = m.get("sign", "")
                    if sign_val is None:
                        sign_val = ""
                    art_sign = m.get("artifact_sign", "")
                    if art_sign is None:
                        art_sign = ""
                    return {
                        "sign": str(sign_val).strip(),
                        "artifact_sign": str(art_sign).strip(),
                        "type": str(m.get("type", "")).strip().lower(),
                        "level": str(m.get("level", "")),
                    }
        except Exception:
            pass

    # Fallback: extract any known sign characters
    all_known_signs = CORE_SIGNS.union(CONFIG_SIGN_CHARS).union(ARTIFACT_SIGNS)
    signs = "".join(ch for ch in entity if ch in all_known_signs)
    return {"sign": signs, "artifact_sign": "", "type": "", "level": ""}

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
        async with db.execute('SELECT path, content, level FROM files') as cur:
            rows = await cur.fetchall()
    
    # Each update: (sign, sign_source, sign_auto, artifact_sign, path)
    sign_updates: List[tuple] = []
    mix_updates: List[tuple] = []
    
    for path, content, level in rows:
        if not content:
            continue
        if _is_meta_root(path):
            continue
        if level in (0, 1):
            continue
        
        if level == 5:
            # Artifact: artifact_sign by heuristic, no core_sign
            art_sign = _determine_artifact_sign(content, {})
            sign_updates.append((art_sign, "auto", art_sign, art_sign, path))
        else:
            # L1-L4: core_sign from embedding
            core_result = await _determine_core_by_embedding(content, db_path)
            above = core_result.get("above_threshold", [])
            sign_auto = "".join(above) if above else (list(CORE_SIGNS)[0] if CORE_SIGNS else "")
            confident = core_result.get("confident", False)
            source = "auto" if confident else "weak_auto"
            percentages = core_result.get("percentages", {})
            
            # L4 keeps only its own optional artifact_sign in YAML.
            # Child artifact inventory is represented by core_mix/drift/UI, not by sign.
            art_sign = ""
            if level == 4:
                try:
                    raw = Path(path).read_text(encoding="utf-8")
                    if raw.startswith("---"):
                        end = raw.find("\n---\n", 4)
                        if end > 0:
                            fm = yaml.safe_load(raw[4:end]) or {}
                            art_sign = str(fm.get("artifact_sign") or "")
                except Exception:
                    pass
            
            composite = art_sign + sign_auto if art_sign else sign_auto
            sign_updates.append((composite, source, sign_auto, art_sign, path))
            
            if percentages and RULE["core_mix"]:
                mix_updates.append((json.dumps(percentages), path))
        
        updated += 1
    
    if not dry_run and sign_updates:
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                'UPDATE files SET sign = ?, sign_source = ?, sign_auto = ?, artifact_sign = ? WHERE path = ?',
                sign_updates
            )
            if mix_updates:
                await db.executemany(
                    'UPDATE files SET core_mix = ? WHERE path = ?',
                    mix_updates
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
        if _is_meta_root(path):
            continue
        # Skip L5 (artifacts) — they have no core_sign, so no core_mix to aggregate
        if level == 5:
            continue
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


async def _process_orphans(
    db_path: str, dry_run: bool = False, auto_parents: bool = True, limit: int = 50
) -> Dict[str, Any]:
    """Find files with empty/missing sign and auto-fill metadata.
    
    Workflow: Obsidian-first → user creates note → this tool fills the gap.
    Skips meta-root and files that already have a sign set.
    """
    if not RULE["has_sign_auto"]:
        return {"error": "Not available in luca mode"}

    root = Path(OBSIDIAN_ROOT)
    orphans = []

    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith('.') or part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if _is_meta_root(p):
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            fm, content = _parse_frontmatter(raw)
            sign = str(fm.get("sign", "")).strip()
            level = fm.get("level", 0)
            if isinstance(level, str):
                level = int(level) if level.isdigit() else 0
            has_parents = bool(fm.get("parents") or fm.get("parents_meta"))
            if not sign:
                orphans.append((p, raw, fm, content, "no_sign"))
            elif level >= 2 and not has_parents:
                orphans.append((p, raw, fm, content, "no_parents"))
        except Exception:
            continue
        if len(orphans) >= limit:
            break

    if not orphans:
        return {"processed": 0, "orphans": []}

    results = []
    for p, raw, fm, content, reason in orphans:
        rel = str(p.relative_to(root))
        if not content:
            content = ""

        level = _get_level_from_meta(fm)

        if reason == "no_sign":
            sign_result = await _determine_sign_smart(content, {**fm, "path": str(p)}, db_path, level=level)
        else:
            sign_result = {"actual_sign": fm.get("sign", ""), "source": "existing", "artifact_sign": fm.get("artifact_sign", "")}

        tags = fm.get("tags", [])
        if not tags and content:
            tags = await _extract_tags(content)

        parents_meta = _get_parents_meta(fm)
        parents_auto = False
        if not parents_meta and auto_parents and content:
            if RULE["reference_vectors"]:
                vec = await _get_embedding(content[:EMBED_MAX_CHARS])
                if vec:
                    async with aiosqlite.connect(db_path) as db:
                        async with db.execute(
                            'SELECT f.path, f.sign, f.level, e.embedding FROM files f '
                            'LEFT JOIN embeddings e ON f.path = e.path '
                            'WHERE e.embedding IS NOT NULL AND f.path <> ? '
                            'AND COALESCE(f.sign, "") <> "" AND f.level < ?',
                            (str(p), level)
                        ) as cur:
                            rows = await cur.fetchall()

                    core_result = await _determine_core_by_embedding(content, db_path)

                    if reason != "no_sign" and sign_result.get("actual_sign"):
                        man_cores = [ch for ch in sign_result["actual_sign"] if ch in CORE_SIGNS]
                        dominant_core = man_cores[0] if man_cores else core_result.get("dominant")
                    else:
                        dominant_core = core_result.get("dominant")

                    best = None
                    best_score = 0.0
                    best_same_core = False
                    for p_path, p_sign, p_level, emb_json in rows:
                        try:
                            other_vec = json.loads(emb_json)
                        except Exception:
                            continue
                        sim = _cosine(vec, other_vec)
                        p_cores = [ch for ch in (p_sign or "") if ch in CORE_SIGNS]
                        same_core = dominant_core and dominant_core in p_cores if p_cores else False
                        if sim > best_score or (sim == best_score and same_core and not best_same_core):
                            best_score = sim
                            best_same_core = same_core
                            best = Path(p_path).stem

                    if best and best_score >= PARENT_LINK_THRESHOLD:
                        parents_meta = [{"entity": best, "link_type": "hierarchy"}]
                        parents_auto = True
            
            # 2. Temporary link: domain anchor (L1/L2 with matching core)
            anchor = await _find_temporary_anchor(content, db_path, level)
            if anchor and not any(p.get("entity") == anchor for p in parents_meta):
                parents_meta.append({"entity": anchor, "link_type": "temporary"})
                parents_auto = True

        entry = {
            "path": rel,
            "level": level,
            "sign": sign_result["actual_sign"],
            "artifact_sign": sign_result.get("artifact_sign", ""),
            "sign_source": sign_result.get("source", ""),
            "tags": tags,
            "hierarchy_parents": [p["entity"] for p in parents_meta if p.get("link_type") == "hierarchy"],
            "temporary_parents": [p["entity"] for p in parents_meta if p.get("link_type") == "temporary"],
            "parents_auto": parents_auto,
        }

        if not dry_run:
            meta = {
                **fm,
                "level": level,
                "sign": sign_result["actual_sign"],
                "tags": tags,
                "parents_meta": parents_meta,
            }
            if sign_result.get("artifact_sign"):
                meta["artifact_sign"] = sign_result["artifact_sign"]
            success, error = await write_file_with_metadata(p, content, meta, db_path)
            entry["status"] = "ok" if success else f"error: {error}"
        else:
            entry["status"] = "preview"

        results.append(entry)

    return {"processed": len(results), "dry_run": dry_run, "orphans": results}


# ============================================================================
# MCP Server
# ============================================================================

async def _index_all_files(db_path: str, with_embeddings: bool = False) -> Dict[str, Any]:
    root = Path(OBSIDIAN_ROOT)
    total = 0
    embedded = 0
    errors = 0
    seen_paths: Set[str] = set()
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith('.') or part in EXCLUDED_DIRS for part in rel_parts):
            continue
        try:
            data = await read_file_with_metadata(p)
            await _index_file(db_path, p, data)
            seen_paths.add(str(p))
            total += 1
            # Meta-root (level 0) is indexed for graph visibility but never embedded
            if _is_meta_root(p):
                continue
            if with_embeddings and data.get('content') and RULE["reference_vectors"]:
                if not await _embedding_is_fresh(db_path, str(p)):
                    clean_content = data['content']
                    vec = await _get_embedding(clean_content[:EMBED_MAX_CHARS])
                    if vec:
                        await _save_embedding(db_path, str(p), vec)
                        embedded += 1
                else:
                    embedded += 1
        except Exception as e:
            logger.warning(f"Indexing error {p}: {e}")
            errors += 1

    if seen_paths:
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
            await db.commit()

    orphans = await _find_orphaned_links(db_path)
    return {"indexed": total, "embedded": embedded, "errors": errors, "orphans": orphans}

async def run_server():
    db_path = _default_db_path()
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
                description="Read one Markdown note from the local knowledge base. Returns the note body, YAML frontmatter, "
                            "hierarchy metadata, parent links, tags, and warnings as JSON. Use this before write_file when you need "
                            "to preserve existing content or inspect current metadata. Side effect: refreshes this file in the local "
                            "SQLite index so later classification and parent suggestions use current data. It never changes the file.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT, e.g. 'notes/my-note.md'"}},
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="write_file",
                description="Create or replace one Markdown note with YAML metadata. Use it when an agent has an explicit final "
                            "content body and metadata to save. This is a destructive write: it replaces the complete file, unless "
                            "content_lock=true is used to preserve the existing body and update only metadata. Before writing, the "
                            "server validates that parent links do not create graph cycles, syncs parents and parents_meta, and then "
                            "refreshes the local index. Use read_file first for existing notes and suggest_metadata first when you "
                            "want classification hints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "content": {"type": "string", "description": "Markdown body (without frontmatter delimiters)"},
                        "metadata": {
                            "type": "object",
                            "description": "YAML frontmatter to write for the note.",
                            "properties": {
                                "type": {"type": "string", "description": "Entity type such as core, pattern, module, quant, or artifact.", "enum": ["meta", "core", "pattern", "module", "quant", "artifact"]},
                                "level": {"type": "integer", "description": "Hierarchy level: 0=meta root, 1=core/domain, 2=pattern/topic, 3=module/group, 4=quant/idea, 5=artifact/raw material.", "enum": [0, 1, 2, 3, 4, 5]},
                                "sign": {"type": "string", "description": "Domain sign assigned manually or by semantic classification, e.g. S, D, E."},
                                "artifact_sign": {"type": "string", "description": "Material type sign for artifacts/quants, e.g. note, concept, reference, log, news, hypothesis, specification."},
                                "parents": {"type": "array", "items": {"type": "string"}, "description": "Obsidian wiki links for parents, e.g. ['[[Systems]]']."},
                                "parents_meta": {"type": "array", "items": {"type": "object", "properties": {"entity": {"type": "string"}, "link_type": {"type": "string", "enum": ["hierarchy", "semantic", "temporary", "tag", "error"]}}}, "description": "Structured parent links used by NOUZ."},
                                "tags": {"type": "array", "items": {"type": "string"}, "description": "Search and semantic tags."}
                            }
                        },
                        "content_lock": {"type": "boolean", "description": "If true, IGNORE content param and preserve original file text. Default false.", "default": False}
                    },
                    "required": ["path", "content"]
                }
            ),
            types.Tool(
                name="update_metadata",
                description="Update only YAML frontmatter for an existing note and preserve the Markdown body exactly. "
                            "Use this for safe changes to type, level, sign, artifact_sign, tags, parents, and parents_meta "
                            "when the note content must not be touched.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "metadata": {
                            "type": "object",
                            "description": "YAML frontmatter to write while preserving the existing body."
                        }
                    },
                    "required": ["path", "metadata"]
                }
            ),
            types.Tool(
                name="list_files",
                description="List notes already known to the local index. Returns lightweight records with path, type, level, and sign, "
                            "without loading full note bodies. Use this for inventory, filtering, and finding files to inspect next. "
                            "Set no_metadata=true to find Markdown files without YAML metadata. Use get_children or get_parents when "
                            "you need graph traversal from a specific note.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "no_metadata": {"type": "boolean", "description": "If true, include files without YAML frontmatter"},
                        "level": {"type": "integer", "description": "Filter by hierarchy level: 1=core/domain, 2=pattern/topic, 3=module/group, 4=quant/idea, 5=artifact/raw material", "enum": [1, 2, 3, 4, 5]},
                        "sign": {"type": "string", "description": "Filter by domain sign, e.g. S, D, E, or another sign configured in config.yaml"},
                        "subfolder": {"type": "string", "description": "Restrict search to a subfolder within the vault"}
                    }
                }
            ),
            types.Tool(
                name="get_children",
                description="Traverse the hierarchy downward from one note. Returns all direct and transitive child note paths from "
                            "the local graph index. Use this to answer 'what does this topic/module contain?' It is read-only and "
                            "does not recompute semantic classification. Use get_parents for the opposite direction.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"}},
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="get_parents",
                description="Return the parent links of one note from the graph index. Each result includes the parent entity name "
                            "and link_type, such as hierarchy, semantic, temporary, tag, or error. Use this to understand "
                            "where a note belongs before editing links. It is read-only. Use suggest_parents when a note has no "
                            "parents and you want candidate links.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"}},
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="suggest_metadata",
                description="Analyze one note and propose knowledge-graph metadata for review. Returns suggested domain sign, "
                            "material type, hierarchy level, tags, bridge candidates, and hierarchy warnings. Use this before "
                            "write_file when you want classification help, or to audit an existing note. It is read-only and never "
                            "edits YAML. Semantic fields require embeddings and are available in PRIZMA/SLOI modes. The optional "
                            "context object lets an agent test metadata overrides without changing the note.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "context": {
                            "type": "object",
                            "description": "Optional metadata overrides for what-if analysis.",
                            "properties": {
                                "type": {"type": "string", "enum": ["meta", "core", "pattern", "module", "quant", "artifact"]},
                                "level": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
                                "sign": {"type": "string", "description": "Candidate domain sign to evaluate."},
                                "parents": {"type": "array", "items": {"type": "string"}, "description": "Candidate parent wiki links."},
                                "tags": {"type": "array", "items": {"type": "string"}, "description": "Candidate tags."}
                            }
                        }
                    },
                    "required": ["path"]
                }
            ),
            types.Tool(
                name="embed",
                description="Create a vector embedding for a short text using the configured embedding provider. Returns the vector "
                            "and its dimension. Use this only for diagnostics, manual similarity checks, or validating an embedding "
                            "setup. It does not index notes and has no side effects. For batch note embeddings, use index_all with "
                            "with_embeddings=true.",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to embed (will be truncated to ~2000 chars)"}},
                    "required": ["text"]
                }
            ),
            types.Tool(
                name="index_all",
                description="Scan the whole Markdown vault and rebuild the local SQLite index of files, metadata, and graph links. "
                            "Use this after adding, moving, or reorganizing notes outside NOUZ. It is safe to run repeatedly and "
                            "reports missing parent links. With with_embeddings=true it also updates vector embeddings for semantic "
                            "classification, which is slower and requires an embedding provider. This tool indexes data; it is not "
                            "a search tool.",
                inputSchema={
                    "type": "object",
                    "properties": {"with_embeddings": {"type": "boolean", "description": "If true, compute embeddings for all files (slower, requires LM Studio/Ollama). Default false."}}
                }
            ),
        ]
        
        if RULE["reference_vectors"]:
            tools.extend([
                types.Tool(
                    name="suggest_parents",
                    description="Suggest parent notes for one note by vector similarity. Returns ranked candidates with scores so a "
                            "human or agent can choose links for orphan or weakly connected notes. It never writes YAML and requires "
                            "embeddings in PRIZMA/SLOI modes. Use get_parents to inspect existing links; use write_file only after "
                            "you decide which parent links to keep.",
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
                    description="Build or refresh reference embeddings for the semantic domains defined in config.yaml. Use this after "
                            "creating a config, changing domain descriptions, or changing embedding models. It writes only to the "
                            "local database, not to Markdown files. The result includes raw and mean-centered cosine similarities "
                            "so you can see whether the configured domains are distinct enough for classification.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="recalc_core_mix",
                    description="Recalculate each higher-level node's domain composition from its children. This reveals structural "
                            "drift: a module may be labeled as one domain while its child notes now mostly belong to another. "
                            "Use after index_all with embeddings or after recalc_signs. It updates only the local database and "
                            "does not modify Markdown YAML.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="recalc_signs",
                    description="Reclassify all indexed notes against the current domain configuration. Use this after calibrate_cores, "
                            "after adding many notes, or after changing the classification rules. It updates automatic classification "
                            "fields in the local database and does not edit Markdown YAML. Set dry_run=true to preview the changes. "
                            "For one note, use suggest_metadata instead.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "dry_run": {"type": "boolean", "description": "Preview only, don't write to DB (default false)"}
                        }
                    }
                ),
            ])

        if RULE["has_sign_auto"]:
            tools.append(
                types.Tool(
                    name="process_orphans",
                    description="Batch-process notes that are missing key metadata. It can propose or write domain signs, material "
                                "types, tags, and parent links for newly created or orphaned Markdown files. Use dry_run=true first "
                                "to review proposed changes. With dry_run=false this writes YAML frontmatter to files, so it should "
                                "be used after inspection or on a trusted batch.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "dry_run": {"type": "boolean", "description": "Preview only, don't write files (default false)"},
                            "auto_parents": {"type": "boolean", "description": "Auto-link orphans to suggested parent (default true)"},
                            "limit": {"type": "integer", "description": "Max files to process (default 50)"}
                        }
                    }
                )
            )
            tools.append(
                types.Tool(
                    name="add_entity",
                    description="Create a new knowledge-base entity as a Markdown file, then assign initial graph metadata. Use this "
                                "for new notes when the agent has the content and should let NOUZ infer level, domain sign, tags, "
                                "and optional parent links. Set auto_parents=false when a human will choose parents manually. This "
                                "writes a new file and returns the metadata that was applied.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT, e.g. 'notes/my-note.md'"},
                            "content": {"type": "string", "description": "Markdown body (without frontmatter delimiters)"},
                            "level": {"type": "integer", "description": "Hierarchy level 1-5 (default 5=artifact)", "enum": [1, 2, 3, 4, 5]},
                            "parents": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "entity": {"type": "string", "description": "Parent entity name or path."},
                                        "link_type": {"type": "string", "description": "Relationship type.", "enum": ["hierarchy", "semantic", "temporary", "tag", "error"]}
                                    },
                                    "required": ["entity"]
                                },
                                "description": "Explicit parent links. Optional; auto-suggested if empty and auto_parents=true."
                            },
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (auto-extracted if empty)"},
                            "sign": {"type": "string", "description": "Manual sign override (auto-determined if empty)"},
                            "auto_parents": {"type": "boolean", "description": "If true and no explicit parents, auto-link to top suggested parent (default true)"},
                            "type": {"type": "string", "description": "Entity type (default 'artifact' for L5)", "enum": ["core", "pattern", "module", "quant", "artifact"]}
                        },
                        "required": ["path", "content"]
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
                content_lock = args.get("content_lock", False)
                
                if content_lock and full.exists():
                    # IGNORE content param, read original file text
                    async with aiofiles.open(full, 'r', encoding='utf-8') as f:
                        file_full_text = await f.read()
                    _, content = _split_frontmatter_raw(file_full_text)
                
                success, error = await write_file_with_metadata(full, content, meta, db_path)
                if success:
                    return [types.TextContent(type="text", text=json.dumps({"status": "ok", "path": rel}, ensure_ascii=False))]
                else:
                    return [types.TextContent(type="text", text=json.dumps({"status": "error", "reason": error}, ensure_ascii=False))]

            elif name == "update_metadata":
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]

                async with aiofiles.open(full, 'r', encoding='utf-8') as f:
                    file_full_text = await f.read()
                _, body = _split_frontmatter_raw(file_full_text)

                meta = args.get("metadata", {})
                success, error = await write_file_with_metadata(full, body, meta, db_path)
                if success:
                    return [types.TextContent(type="text", text=json.dumps({"status": "ok", "path": rel, "body_preserved": True}, ensure_ascii=False))]
                return [types.TextContent(type="text", text=json.dumps({"status": "error", "reason": error}, ensure_ascii=False))]

            elif name == "list_files":
                root = Path(OBSIDIAN_ROOT)
                filter_level = args.get("level")
                filter_sign = args.get("sign")
                subfolder = args.get("subfolder", "")
                no_metadata = args.get("no_metadata", False)
                
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
                
                db_data = {row[0]: row[1:] for row in rows}
                files = []
                for abs_path, rel in all_files:
                    if abs_path not in db_data:
                        if no_metadata:
                            files.append({"path": rel, "type": None, "level": None, "sign": None})
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

            elif name == "add_entity":
                if not RULE["has_sign_auto"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": "Not available in luca mode. Use write_file instead."}, ensure_ascii=False))]
                rel = args.get("path", "")
                full = _safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]

                content = args.get("content", "")
                level = args.get("level", 5)
                if not isinstance(level, int) or level < 1 or level > 5:
                    level = 5
                explicit_parents = args.get("parents", [])
                explicit_tags = args.get("tags", [])
                manual_sign = args.get("sign", "")
                auto_parents = args.get("auto_parents", True)
                entity_type = args.get("type", LEVEL_TO_TYPE.get(level, "artifact"))

                meta = {"level": level, "type": entity_type, "path": str(full)}
                if manual_sign:
                    meta["sign"] = manual_sign

                sign_result = await _determine_sign_smart(content, meta, db_path, level=level)
                meta["sign"] = sign_result["actual_sign"]
                if sign_result.get("artifact_sign"):
                    meta["artifact_sign"] = sign_result["artifact_sign"]

                tags = explicit_tags
                if not tags and content:
                    tags = await _extract_tags(content)
                meta["tags"] = tags

                if explicit_parents:
                    meta["parents_meta"] = explicit_parents
                    meta["parents"] = [p.get("entity", "") if isinstance(p, dict) else str(p) for p in explicit_parents]
                elif auto_parents:
                    parents_meta = []
                    # 1. Hierarchy link: closest entity by embedding similarity
                    if RULE["reference_vectors"]:
                        vec = await _get_embedding(content[:EMBED_MAX_CHARS])
                        if vec:
                            core_result = await _determine_core_by_embedding(content, db_path)
                            dominant_core = core_result.get("dominant")

                            async with aiosqlite.connect(db_path) as db:
                                async with db.execute(
                                    'SELECT f.path, f.sign, f.level, e.embedding FROM files f '
                                    'LEFT JOIN embeddings e ON f.path = e.path '
                                    'WHERE e.embedding IS NOT NULL AND f.path <> ? '
                                    'AND COALESCE(f.sign, "") <> "" AND f.level < ?',
                                    (str(full), level)
                                ) as cur:
                                    rows = await cur.fetchall()

                            best = None
                            best_score = 0.0
                            best_same_core = False
                            for p_path, p_sign, p_level, emb_json in rows:
                                try:
                                    other_vec = json.loads(emb_json)
                                except Exception:
                                    continue
                                sim = _cosine(vec, other_vec)
                                p_cores = [ch for ch in (p_sign or "") if ch in CORE_SIGNS]
                                same_core = dominant_core and dominant_core in p_cores if p_cores else False
                                if sim > best_score or (sim == best_score and same_core and not best_same_core):
                                    best_score = sim
                                    best_same_core = same_core
                                    best = Path(p_path).stem

                            if best and best_score >= PARENT_LINK_THRESHOLD:
                                parents_meta.append({"entity": best, "link_type": "hierarchy"})

                    # 2. Temporary link: domain anchor (L1/L2 with matching core)
                    anchor = await _find_temporary_anchor(content, db_path, level)
                    if anchor and not any(p.get("entity") == anchor for p in parents_meta):
                        parents_meta.append({"entity": anchor, "link_type": "temporary"})

                    if parents_meta:
                        meta["parents_meta"] = parents_meta
                        meta["parents"] = [p["entity"] for p in parents_meta]

                success, error = await write_file_with_metadata(full, content, meta, db_path)
                if success:
                    parents_meta_result = meta.get("parents_meta", [])
                    hierarchy_parents = [p["entity"] for p in parents_meta_result if p.get("link_type") == "hierarchy"]
                    temporary_parents = [p["entity"] for p in parents_meta_result if p.get("link_type") == "temporary"]
                    result = {
                        "status": "created",
                        "path": rel,
                        "level": level,
                        "sign": meta.get("sign", ""),
                        "artifact_sign": meta.get("artifact_sign", ""),
                        "sign_source": sign_result.get("source", ""),
                        "tags": tags,
                        "hierarchy_parents": hierarchy_parents,
                        "temporary_parents": temporary_parents,
                    }
                    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
                else:
                    return [types.TextContent(type="text", text=json.dumps({"status": "error", "reason": error}, ensure_ascii=False))]

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

            elif name == "process_orphans":
                if not RULE["has_sign_auto"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": "Not available in luca mode"}, ensure_ascii=False))]
                dry_run = args.get("dry_run", False)
                auto_parents = args.get("auto_parents", True)
                limit = args.get("limit", 50)
                result = await _process_orphans(db_path, dry_run, auto_parents, limit)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            else:
                return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False))]

        except Exception as e:
            logger.exception(f"Tool error: {name}")
            return [types.TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run_server())


def main():
    asyncio.run(run_server())
