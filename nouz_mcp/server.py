#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nouz -- Unified MCP Server for Obsidian. v3.1.0

Three modes:
- luca: Graph-based, level is for display only, no semantic classification
- prizma: Graph-based with semantic bridges and core_mix
- sloi: Strict 5-level hierarchy with semantic classification
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

from mcp.server import Server
import mcp.server.stdio
from mcp import types

from nouz_mcp._version import __version__
from nouz_mcp.config import (
    DEFAULT_ARTIFACT_KEYWORDS,
    DEFAULT_ARTIFACT_SIGNS,
    DEFAULT_CONFIG,
    load_config as load_nouz_config,
)
from nouz_mcp.chunks import chunk_markdown
from nouz_mcp.links import check_parents_exist, get_parents_meta
from nouz_mcp.markdown import dump_metadata, parse_frontmatter, split_frontmatter_raw, sync_parents_fields
from nouz_mcp.modes import build_rules, get_level, get_type_by_level
from nouz_mcp.paths import default_db_path, safe_path
from nouz_mcp.serialization import serialize
from nouz_mcp.semantics import call_llm as call_semantic_llm
from nouz_mcp.semantics import extract_tags as extract_semantic_tags
from nouz_mcp.semantics import get_embedding as get_semantic_embedding
from nouz_mcp.signs import (
    determine_artifact_sign,
    signs_share_core,
)
from nouz_mcp.sqlite_store import (
    aggregate_core_mix as aggregate_store_core_mix,
    check_cycle_exists,
    delete_missing_index_entries,
    embedding_is_fresh as store_embedding_is_fresh,
    find_orphaned_links,
    find_entity_path_by_stem,
    get_db_children,
    get_db_parents,
    get_core_mix as get_store_core_mix,
    get_file_summaries,
    index_file as index_store_file,
    list_core_anchor_candidates,
    init_db as init_sqlite_db,
    list_embedding_candidates,
    list_file_levels_desc,
    list_parent_embedding_candidates,
    list_semantic_bridge_rows,
    list_sign_recalc_rows,
    list_tag_bridge_rows,
    load_reference_vectors as load_store_reference_vectors,
    load_embedding as load_store_embedding,
    save_reference_vector as save_store_reference_vector,
    save_embedding as save_store_embedding,
    update_core_mixes,
    update_sign_recalc_rows,
)
from nouz_mcp.use_cases import add_entity as add_entity_use_case
from nouz_mcp.use_cases import index_all_files as index_all_files_use_case
from nouz_mcp.use_cases import list_files as list_files_use_case
from nouz_mcp.use_cases import process_orphans as process_orphans_use_case
from nouz_mcp.use_cases import read_file as read_file_use_case
from nouz_mcp.use_cases import recalc_core_mix as recalc_core_mix_use_case
from nouz_mcp.use_cases import recalc_signs as recalc_signs_use_case
from nouz_mcp.use_cases import suggest_metadata as suggest_metadata_use_case
from nouz_mcp.use_cases import suggest_parents as suggest_parents_use_case
from nouz_mcp.use_cases import update_metadata as update_metadata_use_case
from nouz_mcp.use_cases import write_file as write_file_use_case
from nouz_mcp.use_cases import write_file_with_metadata as write_file_with_metadata_use_case
from nouz_mcp.vault_io import read_text as read_vault_text
from nouz_mcp.vault_io import read_file_with_metadata as read_vault_file_with_metadata
from nouz_mcp.vault_io import write_text as write_vault_text
from nouz_mcp.vectors import cosine, mean_center

VERSION = __version__


READ_ONLY_DISABLED_TOOLS = frozenset(
    {
        "write_file",
        "update_metadata",
        "index_all",
        "calibrate_cores",
        "recalc_core_mix",
        "recalc_signs",
        "process_orphans",
        "add_entity",
    }
)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


READ_ONLY = env_flag("NOUZ_READ_ONLY")


def is_read_only_disabled_tool(name: str) -> bool:
    """Return True for tools hidden and blocked in public read-only mode."""
    return name in READ_ONLY_DISABLED_TOOLS


def filter_read_only_tools(tools: list[types.Tool], *, read_only: bool) -> list[types.Tool]:
    """Hide mutating tools when NOUZ_READ_ONLY is enabled."""
    if not read_only:
        return tools
    return [tool for tool in tools if not is_read_only_disabled_tool(tool.name)]


def read_only_tool_error(name: str) -> list[types.TextContent]:
    return [
        types.TextContent(
            type="text",
            text=json.dumps(
                {"error": f"Tool '{name}' is disabled because NOUZ_READ_ONLY=true."},
                ensure_ascii=False,
            ),
        )
    ]


# ============================================================================
# Mode Configuration
# ============================================================================

RULES = build_rules(lambda et, pa: _check_hierarchy_strict(et, pa))

CONFIG = load_nouz_config()
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
ARTIFACT_SIGN_BY_NAME = {
    str(e.get("name", "")).lower(): str(e["sign"])
    for e in ARTIFACT_SIGN_LIST
    if e.get("name") and e.get("sign")
}
ARTIFACT_KEYWORDS_BY_NAME = {
    str(e.get("name", "")).lower(): [str(kw).lower() for kw in e.get("keywords", [])]
    for e in ARTIFACT_SIGN_LIST
    if e.get("name") and e.get("keywords")
}
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
    "Шаблоны",
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
# LLM & Embeddings
# ============================================================================

async def _call_llm(prompt: str) -> str:
    return await call_semantic_llm(prompt, LLM_API_URL, LLM_MODEL, logger)

async def _extract_tags(content: str) -> List[str]:
    return await extract_semantic_tags(content, LLM_MODEL, _call_llm)

async def _get_embedding(text: str) -> List[float]:
    return await get_semantic_embedding(
        text,
        enabled=EMBED_ENABLED,
        provider=EMBED_PROVIDER,
        model=EMBED_MODEL,
        api_url=EMBED_API_URL,
        api_key=EMBED_API_KEY,
        cache=embed_cache,
        logger=logger,
    )

# ============================================================================
# Files & YAML Processing
# ============================================================================

async def read_file_with_metadata(file_path: Path) -> Dict[str, Any]:
    return await read_vault_file_with_metadata(
        file_path,
        parse_frontmatter=parse_frontmatter,
        serialize_value=serialize,
        logger=logger,
    )

async def write_file_with_metadata(
    file_path: Path,
    content: str,
    metadata: Dict[str, Any],
    db_path: str = "",
    *,
    clean_content: bool = False,
) -> tuple[bool, str]:
    return await write_file_with_metadata_use_case(
        db_path,
        Path(OBSIDIAN_ROOT),
        file_path,
        content,
        metadata,
        clean_content=clean_content,
        sync_parents_fields=sync_parents_fields,
        resolve_entity_path=_resolve_entity_path,
        check_cycle_exists=check_cycle_exists,
        dump_metadata=dump_metadata,
        write_text=write_vault_text,
        index_file=_index_file,
        logger=logger,
    )


# ============================================================================
# SQLite Indexing
# ============================================================================

async def init_db(db_path: str):
    await init_sqlite_db(
        db_path,
        reference_vectors=RULE["reference_vectors"],
        determine_artifact_sign=_determine_artifact_sign,
    )


def _determine_artifact_sign(content: str, meta: Dict) -> str:
    return determine_artifact_sign(
        content,
        meta,
        ARTIFACT_SIGN_BY_NAME,
        ARTIFACT_KEYWORDS_BY_NAME,
        DEFAULT_ARTIFACT_KEYWORDS,
    )


async def _find_temporary_anchor(content: str, db_path: str, level: int = 5) -> Optional[str]:
    """Find a domain anchor for a temporary link based on content's core sign.
    
    For L5 artifacts: the artifact sign is format (n/l/etc), not domain.
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
        for p_path, p_sign, p_level in await list_core_anchor_candidates(db_path):
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


def _check_parents_exist(meta: Dict[str, Any]) -> List[str]:
    return check_parents_exist(OBSIDIAN_ROOT, meta)


async def _index_file(db_path: str, file_path: Path, meta: Dict[str, Any]):
    async def resolve_parent(entity_name: str) -> Optional[str]:
        return await _resolve_entity_path(db_path, entity_name)

    await index_store_file(
        db_path,
        file_path,
        meta,
        get_parents_meta=get_parents_meta,
        resolve_entity_path=resolve_parent,
    )

async def _aggregate_core_mix(db_path: str, parent_path: str, child_level: Optional[int] = None) -> Optional[Dict[str, float]]:
    if not RULE["core_mix"]:
        return None
    return await aggregate_store_core_mix(
        db_path,
        parent_path,
        level_strict=RULE["level_strict"],
        child_level=child_level,
    )


# ============================================================================
# Semantic Functions (Prizma/Sloi only)
# ============================================================================

def _signs_share_core(sign_a: str, sign_b: str) -> bool:
    return signs_share_core(sign_a, sign_b, CORE_SIGNS)

async def _find_semantic_bridges(
    db_path: str, own_path: str, own_sign: str, own_vec: List[float],
    own_sign_source: str = "auto"
) -> List[Dict]:
    if not RULE["semantic_bridges"] or not own_vec:
        return []

    sign_for_blocking = own_sign if own_sign_source != "weak_auto" else ""

    bridges = []
    for path, other_sign, other_sign_source, other_artifact_sign, emb_json in await list_semantic_bridge_rows(db_path):
        if path == own_path:
            continue
        other_sign_for_blocking = (other_sign or "") if other_sign_source != "weak_auto" else ""
        if _signs_share_core(sign_for_blocking, other_sign_for_blocking):
            continue
        try:
            other_vec = json.loads(emb_json)
            sim = cosine(own_vec, other_vec)
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

    Example: note about product architecture and note about release planning:
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

    bridges = []
    seen_paths = set()

    for path, other_sign, other_artifact_sign, tags_json in await list_tag_bridge_rows(db_path):
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
                sim = cosine(own_vec, other_vec)
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
    
    try:
        return await load_store_reference_vectors(db_path)
    except Exception as e:
        logger.warning(f"Failed to load reference_vectors: {e}")
    return {}

async def _save_core_etalon(db_path: str, sign: str, text: str, vec: List[float]):
    if not RULE["reference_vectors"]:
        return
    await save_store_reference_vector(db_path, sign, text, vec)

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
    centered = mean_center(etalons)
    signs = sorted(etalons.keys())
    pairs = {}
    centered_pairs = {}
    for i, s1 in enumerate(signs):
        for s2 in signs[i+1:]:
            sim = cosine(etalons[s1], etalons[s2])
            pairs[f"{s1}-{s2}"] = round(sim, 4)
            csim = cosine(centered[s1], centered[s2])
            centered_pairs[f"{s1}-{s2}"] = round(csim, 4)
    
    return {"calibrated": results, "pairwise_cosine": pairs, "pairwise_cosine_centered": centered_pairs}

def _get_artifact_sign_from_file(path: str) -> str:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(raw)
        return str(fm.get("artifact_sign") or "")
    except Exception:
        pass
    return ""

async def _resolve_entity_path(db_path: str, entity_name: str) -> Optional[str]:
    indexed_path = await find_entity_path_by_stem(db_path, entity_name)
    if indexed_path:
        return indexed_path
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

    centered = mean_center(etalons)
    centroid = [0.0] * len(vec)
    n = len(centered)
    for cv in centered.values():
        for i in range(len(centroid)):
            centroid[i] += cv[i] / n
    vec_centered = [vec[i] - centroid[i] for i in range(len(vec))]

    scores_raw = {sign: cosine(vec, ev) for sign, ev in etalons.items()}
    scores = {sign: cosine(vec_centered, centered[sign]) for sign in centered}

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
    return await suggest_parents_use_case(
        file_path,
        db_path,
        top_n=top_n,
        embed_max_chars=EMBED_MAX_CHARS,
        core_signs=CORE_SIGNS,
        determine_core=_determine_core_by_embedding,
        get_embedding=_get_embedding,
        list_embedding_candidates=list_embedding_candidates,
        parse_frontmatter=parse_frontmatter,
        cosine=cosine,
    )

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
    return get_level(type_str, LEVEL_MAP)

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
    return await suggest_metadata_use_case(
        content,
        context,
        db_path,
        file_path,
        reference_vectors=RULE["reference_vectors"],
        semantic_bridges_enabled=RULE["semantic_bridges"],
        core_mix_enabled=RULE["core_mix"],
        has_sign_auto=RULE["has_sign_auto"],
        core_signs=CORE_SIGNS,
        get_parents_meta=get_parents_meta,
        determine_type=_determine_type,
        extract_tags=_extract_tags,
        embedding_is_fresh=store_embedding_is_fresh,
        get_embedding=_get_embedding,
        save_embedding=save_store_embedding,
        load_embedding=load_store_embedding,
        determine_sign=_determine_sign_smart,
        get_level_from_meta=_get_level_from_meta,
        find_semantic_bridges=_find_semantic_bridges,
        find_tag_bridges=_find_tag_bridges,
        check_hierarchy=_check_hierarchy,
        resolve_entity_path=_resolve_entity_path,
        check_cycle_exists=check_cycle_exists,
        determine_core=_determine_core_by_embedding,
        get_core_mix=get_store_core_mix,
    )


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
            m, _ = parse_frontmatter(raw)
            if not m:
                continue
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
    return await recalc_signs_use_case(
        db_path,
        dry_run=dry_run,
        has_sign_auto=RULE["has_sign_auto"],
        mode_name=MODE,
        core_mix_enabled=RULE["core_mix"],
        core_signs=CORE_SIGNS,
        is_meta_root=_is_meta_root,
        list_sign_rows=list_sign_recalc_rows,
        determine_artifact_sign=_determine_artifact_sign,
        determine_core=_determine_core_by_embedding,
        read_artifact_sign=_get_artifact_sign_from_file,
        update_sign_rows=update_sign_recalc_rows,
    )

async def _recalc_core_mix(db_path: str) -> Dict[str, Any]:
    return await recalc_core_mix_use_case(
        db_path,
        core_mix_enabled=RULE["core_mix"],
        mode_name=MODE,
        level_strict=RULE["level_strict"],
        list_file_levels=list_file_levels_desc,
        is_meta_root=_is_meta_root,
        aggregate_core_mix=_aggregate_core_mix,
        update_core_mixes=update_core_mixes,
    )


async def _process_orphans(
    db_path: str, dry_run: bool = False, auto_parents: bool = True, limit: int = 50
) -> Dict[str, Any]:
    """Find files with empty/missing sign and auto-fill metadata.
    
    Workflow: Obsidian-first → user creates note → this tool fills the gap.
    Skips meta-root and files that already have a sign set.
    """
    return await process_orphans_use_case(
        db_path,
        Path(OBSIDIAN_ROOT),
        dry_run=dry_run,
        auto_parents=auto_parents,
        limit=limit,
        has_sign_auto=RULE["has_sign_auto"],
        reference_vectors=RULE["reference_vectors"],
        embed_max_chars=EMBED_MAX_CHARS,
        parent_link_threshold=PARENT_LINK_THRESHOLD,
        core_signs=CORE_SIGNS,
        excluded_dirs=EXCLUDED_DIRS,
        is_meta_root=_is_meta_root,
        parse_frontmatter=parse_frontmatter,
        get_level_from_meta=_get_level_from_meta,
        determine_sign=_determine_sign_smart,
        extract_tags=_extract_tags,
        get_parents_meta=get_parents_meta,
        get_embedding=_get_embedding,
        determine_core=_determine_core_by_embedding,
        list_parent_candidates=list_parent_embedding_candidates,
        find_temporary_anchor=_find_temporary_anchor,
        cosine=cosine,
        write_file=write_file_with_metadata,
    )


# ============================================================================
# MCP Server
# ============================================================================

async def _index_all_files(db_path: str, with_embeddings: bool = False) -> Dict[str, Any]:
    return await index_all_files_use_case(
        db_path,
        Path(OBSIDIAN_ROOT),
        excluded_dirs=EXCLUDED_DIRS,
        with_embeddings=with_embeddings,
        reference_vectors=RULE["reference_vectors"],
        embed_max_chars=EMBED_MAX_CHARS,
        read_file=read_file_with_metadata,
        index_file=_index_file,
        is_meta_root=_is_meta_root,
        embedding_is_fresh=store_embedding_is_fresh,
        get_embedding=_get_embedding,
        save_embedding=save_store_embedding,
        delete_missing_entries=delete_missing_index_entries,
        find_orphaned_links=find_orphaned_links,
        logger=logger,
    )

async def run_server():
    db_path = default_db_path(OBSIDIAN_ROOT, DATABASE_NAME, DATABASE_PATH)
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
    if READ_ONLY:
        logger.info("Read-only mode enabled: mutating tools are hidden and blocked.")

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
                                "artifact_sign": {"type": "string", "description": "Material type sign for artifacts/quants, e.g. note, concept, reference, log, update, hypothesis, specification."},
                                "parents": {"type": "array", "items": {"type": "string"}, "description": "Obsidian wiki links for parents, e.g. ['[[Systems]]']."},
                                "parents_meta": {"type": "array", "items": {"type": "object", "properties": {"entity": {"type": "string"}, "link_type": {"type": "string", "enum": ["hierarchy", "semantic", "temporary", "tag", "analogy", "error"]}}}, "description": "Structured parent links used by NOUZ."},
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
                            "and link_type, such as hierarchy, semantic, temporary, tag, analogy, or error. Use this to understand "
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
                name="chunk_text",
                description="Split Markdown text into deterministic, embedding-ready chunks. This is a read-only low-level "
                            "retrieval primitive: it does not index, embed, or write anything.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Markdown text to split"},
                        "source_id": {"type": "string", "description": "Optional source identifier used in stable chunk ids"},
                        "max_chars": {"type": "integer", "description": "Target maximum chunk body size before overlap. Default 1200."},
                        "overlap_chars": {"type": "integer", "description": "Prefix overlap for chunks after the first. Default 120."},
                    },
                    "required": ["text"]
                }
            ),
            types.Tool(
                name="chunk_file",
                description="Read one Markdown note and return deterministic, embedding-ready chunks of its body. Read-only: "
                            "does not index, embed, or write anything. Use before designing chunk embeddings or context packs.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from OBSIDIAN_ROOT"},
                        "max_chars": {"type": "integer", "description": "Target maximum chunk body size before overlap. Default 1200."},
                        "overlap_chars": {"type": "integer", "description": "Prefix overlap for chunks after the first. Default 120."},
                    },
                    "required": ["path"]
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
                                        "link_type": {"type": "string", "description": "Relationship type.", "enum": ["hierarchy", "semantic", "temporary", "tag", "analogy", "error"]}
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
        
        return filter_read_only_tools(tools, read_only=READ_ONLY)

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        args = arguments or {}
        try:
            if READ_ONLY and is_read_only_disabled_tool(name):
                return read_only_tool_error(name)

            if name == "read_file":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]
                data = await read_file_use_case(
                    db_path,
                    full,
                    read_file_with_metadata=read_file_with_metadata,
                    index_file=_index_file,
                    check_parents_exist=_check_parents_exist,
                )
                return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]

            elif name == "write_file":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                
                content = args.get("content", "")
                meta = args.get("metadata", {})
                content_lock = args.get("content_lock", False)

                result = await write_file_use_case(
                    db_path,
                    full,
                    rel,
                    content,
                    meta,
                    content_lock=content_lock,
                    read_text=read_vault_text,
                    split_frontmatter=split_frontmatter_raw,
                    write_file_with_metadata=write_file_with_metadata,
                )
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

            elif name == "update_metadata":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]

                meta = args.get("metadata", {})
                result = await update_metadata_use_case(
                    db_path,
                    full,
                    rel,
                    meta,
                    read_text=read_vault_text,
                    split_frontmatter=split_frontmatter_raw,
                    write_file_with_metadata=write_file_with_metadata,
                )
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

            elif name == "list_files":
                files = await list_files_use_case(
                    db_path,
                    Path(OBSIDIAN_ROOT),
                    excluded_dirs=EXCLUDED_DIRS,
                    subfolder=args.get("subfolder", ""),
                    filter_level=args.get("level"),
                    filter_sign=args.get("sign") or "",
                    no_metadata=args.get("no_metadata", False),
                    get_file_summaries=get_file_summaries,
                )
                return [types.TextContent(type="text", text=json.dumps(files, ensure_ascii=False, indent=2))]

            elif name == "get_children":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                children = await get_db_children(db_path, str(full))
                root = Path(OBSIDIAN_ROOT)
                rel_children = [str(Path(c).relative_to(root)) for c in children]
                return [types.TextContent(type="text", text=json.dumps(rel_children, ensure_ascii=False, indent=2))]

            elif name == "get_parents":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                parents = await get_db_parents(db_path, str(full))
                return [types.TextContent(type="text", text=json.dumps(parents, ensure_ascii=False, indent=2))]

            elif name == "suggest_metadata":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
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

            elif name == "chunk_text":
                text = args.get("text", "")
                source_id = args.get("source_id", "")
                chunks = chunk_markdown(
                    text,
                    source_id=source_id,
                    max_chars=int(args.get("max_chars", 1200)),
                    overlap_chars=int(args.get("overlap_chars", 120)),
                )
                result = {"source_id": source_id, "chunk_count": len(chunks), "chunks": chunks}
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "chunk_file":
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]
                if not full.exists():
                    return [types.TextContent(type="text", text=f"File not found: {rel}")]
                data = await read_file_with_metadata(full)
                content = data.get("content", "")
                chunks = chunk_markdown(
                    content,
                    source_id=rel,
                    max_chars=int(args.get("max_chars", 1200)),
                    overlap_chars=int(args.get("overlap_chars", 120)),
                )
                result = {"path": rel, "chunk_count": len(chunks), "chunks": chunks}
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "index_all":
                with_embeddings = args.get("with_embeddings", False)
                result = await _index_all_files(db_path, with_embeddings=with_embeddings)
                return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

            elif name == "add_entity":
                if not RULE["has_sign_auto"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": "Not available in luca mode. Use write_file instead."}, ensure_ascii=False))]
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
                if full is None:
                    return [types.TextContent(type="text", text="Error: Path outside OBSIDIAN_ROOT")]

                content = args.get("content", "")
                result = await add_entity_use_case(
                    db_path,
                    full,
                    rel,
                    content,
                    level=args.get("level", 5),
                    entity_type=args.get("type", ""),
                    manual_sign=args.get("sign", ""),
                    explicit_parents=args.get("parents", []),
                    explicit_tags=args.get("tags", []),
                    auto_parents=args.get("auto_parents", True),
                    has_sign_auto=RULE["has_sign_auto"],
                    reference_vectors=RULE["reference_vectors"],
                    embed_max_chars=EMBED_MAX_CHARS,
                    parent_link_threshold=PARENT_LINK_THRESHOLD,
                    core_signs=CORE_SIGNS,
                    get_type_by_level=get_type_by_level,
                    determine_sign=_determine_sign_smart,
                    extract_tags=_extract_tags,
                    get_embedding=_get_embedding,
                    determine_core=_determine_core_by_embedding,
                    list_parent_candidates=list_parent_embedding_candidates,
                    find_temporary_anchor=_find_temporary_anchor,
                    cosine=cosine,
                    write_file=write_file_with_metadata,
                )
                if result.get("status") == "created":
                    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
                else:
                    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

            elif name == "suggest_parents":
                if not RULE["reference_vectors"]:
                    return [types.TextContent(type="text", text=json.dumps({"error": f"This tool is not available in '{MODE}' mode. Use 'prizma' or 'sloi' mode for semantic classification."}, ensure_ascii=False))]
                rel = args.get("path", "")
                full = safe_path(OBSIDIAN_ROOT, rel)
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
