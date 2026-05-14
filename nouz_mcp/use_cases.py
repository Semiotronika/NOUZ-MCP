"""Application use cases for NOUZ."""

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from nouz_mcp.markdown import explicit_tag_list


ReadFile = Callable[[Path], Awaitable[Dict[str, Any]]]
IndexFile = Callable[[str, Path, Dict[str, Any]], Awaitable[None]]
IsMetaRoot = Callable[[Path], bool]
EmbeddingIsFresh = Callable[[str, str], Awaitable[bool]]
GetEmbedding = Callable[[str], Awaitable[List[float]]]
SaveEmbedding = Callable[[str, str, List[float]], Awaitable[None]]
LoadEmbedding = Callable[[str, str], Awaitable[List[float]]]
DeleteMissingEntries = Callable[[str, Set[str]], Awaitable[None]]
ChunkMarkdown = Callable[..., List[Dict[str, Any]]]
ChunkEmbeddingsAreFresh = Callable[[str, str], Awaitable[bool]]
SaveChunkEmbeddings = Callable[[str, str, List[Dict[str, Any]], List[List[float]]], Awaitable[None]]
ListChunkEmbeddings = Callable[[str, str], Awaitable[List[Dict[str, Any]]]]
FindOrphanedLinks = Callable[[str], Awaitable[List[Dict[str, str]]]]
GetFileSummaries = Callable[[str, List[str]], Awaitable[Dict[str, tuple[str, Optional[int], str]]]]
ListEmbeddingCandidates = Callable[[str], Awaitable[List[tuple[str, str, Optional[int], str, str]]]]
ListParentEmbeddingCandidates = Callable[[str, str, int], Awaitable[List[tuple[str, str, Optional[int], str]]]]
DetermineCore = Callable[[str, str], Awaitable[Dict[str, Any]]]
Cosine = Callable[[List[float], List[float]], float]
ParseFrontmatter = Callable[[str], tuple[Dict[str, Any], str]]
GetTypeByLevel = Callable[[int], str]
WriteFileWithMetadata = Callable[[Path, str, Dict[str, Any], str], Awaitable[tuple[bool, str]]]
ReadText = Callable[[Path], Awaitable[str]]
WriteText = Callable[[Path, str], Awaitable[None]]
SplitFrontmatter = Callable[[str], tuple[Dict[str, Any], str]]
SyncParentsFields = Callable[[Dict[str, Any]], Dict[str, Any]]
DumpMetadata = Callable[[Dict[str, Any]], str]
CheckParentsExist = Callable[[Dict[str, Any]], List[str]]
GetParentsMeta = Callable[[Dict[str, Any]], List[Dict[str, Any]]]
DetermineType = Callable[[str, Dict[str, Any]], str]
GetLevelFromMeta = Callable[[Dict[str, Any]], int]
DetermineSign = Callable[[str, Dict[str, Any], str, int], Awaitable[Dict[str, Any]]]
FindSemanticBridges = Callable[[str, str, str, List[float], str], Awaitable[List[Dict[str, Any]]]]
CheckHierarchy = Callable[[str, List[Dict[str, Any]]], List[Dict[str, Any]]]
ResolveEntityPath = Callable[[str, str], Awaitable[Optional[str]]]
CheckCycleExists = Callable[[str, str, str], Awaitable[bool]]
GetCoreMix = Callable[[str, str], Awaitable[Optional[Dict[str, float]]]]
FindTemporaryAnchor = Callable[[str, str, int], Awaitable[Optional[str]]]
ListFileLevels = Callable[[str], Awaitable[List[tuple[str, Optional[int]]]]]
ListSignRecalcRows = Callable[[str], Awaitable[List[tuple[str, str, Optional[int]]]]]
AggregateCoreMix = Callable[[str, str, Optional[int]], Awaitable[Dict[str, float] | None]]
UpdateCoreMixes = Callable[[str, List[tuple[str, str]]], Awaitable[None]]
DetermineArtifactSign = Callable[[str, Dict[str, Any]], str]
ReadArtifactSign = Callable[[str], str]
UpdateSignRecalcRows = Callable[[str, List[tuple[str, str, str, str, str]], List[tuple[str, str]]], Awaitable[None]]


async def index_all_files(
    db_path: str,
    root: Path,
    *,
    excluded_dirs: set[str],
    with_embeddings: bool,
    embed_max_chars: int,
    read_file: ReadFile,
    index_file: IndexFile,
    is_meta_root: IsMetaRoot,
    embedding_is_fresh: EmbeddingIsFresh,
    get_embedding: GetEmbedding,
    save_embedding: SaveEmbedding,
    delete_missing_entries: DeleteMissingEntries,
    find_orphaned_links: FindOrphanedLinks,
    chunk_markdown: ChunkMarkdown | None = None,
    chunk_embeddings_are_fresh: ChunkEmbeddingsAreFresh | None = None,
    save_chunk_embeddings: SaveChunkEmbeddings | None = None,
    chunk_max_chars: int = 1200,
    chunk_overlap_chars: int = 120,
    logger: logging.Logger | None = None,
) -> Dict[str, Any]:
    """Scan the vault, refresh the SQLite index, and optionally refresh embeddings."""
    total = 0
    embedded = 0
    chunk_files = 0
    chunk_embedded = 0
    errors = 0
    seen_paths: Set[str] = set()

    for path in root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") or part in excluded_dirs for part in rel_parts):
            continue
        try:
            data = await read_file(path)
            await index_file(db_path, path, data)
            seen_paths.add(str(path))
            total += 1

            if is_meta_root(path):
                continue
            if with_embeddings:
                content = str(data.get("content") or "")
                if content:
                    if not await embedding_is_fresh(db_path, str(path)):
                        vec = await get_embedding(content[:embed_max_chars])
                        if vec:
                            await save_embedding(db_path, str(path), vec)
                            embedded += 1
                    else:
                        embedded += 1
                if chunk_markdown and chunk_embeddings_are_fresh and save_chunk_embeddings:
                    if not content:
                        await save_chunk_embeddings(db_path, str(path), [], [])
                    elif not await chunk_embeddings_are_fresh(db_path, str(path)):
                        chunks = chunk_markdown(
                            content,
                            source_id=str(path),
                            max_chars=chunk_max_chars,
                            overlap_chars=chunk_overlap_chars,
                        )
                        chunk_vectors = []
                        embedded_chunks = []
                        for chunk in chunks:
                            vec = await get_embedding(str(chunk.get("text", ""))[:embed_max_chars])
                            if vec:
                                embedded_chunks.append(chunk)
                                chunk_vectors.append(vec)
                        await save_chunk_embeddings(db_path, str(path), embedded_chunks, chunk_vectors)
                        if embedded_chunks:
                            chunk_files += 1
                            chunk_embedded += len(embedded_chunks)
        except Exception as exc:
            if logger:
                logger.warning(f"Indexing error {path}: {exc}")
            errors += 1

    await delete_missing_entries(db_path, seen_paths)

    orphans = await find_orphaned_links(db_path)
    return {
        "indexed": total,
        "embedded": embedded,
        "chunk_files": chunk_files,
        "chunk_embedded": chunk_embedded,
        "errors": errors,
        "orphans": orphans,
    }


async def search_chunk_embeddings(
    query: str,
    db_path: str,
    *,
    top_k: int = 8,
    path: str = "",
    embed_max_chars: int,
    get_embedding: GetEmbedding,
    list_chunk_embeddings: ListChunkEmbeddings,
    cosine: Cosine,
) -> Dict[str, Any]:
    """Rank stored chunk embeddings by semantic similarity to a query."""
    normalized_query = query.strip()
    limit = max(1, min(int(top_k), 50))
    if not normalized_query:
        return {"error": "Empty query.", "matches": []}

    query_vec = await get_embedding(normalized_query[:embed_max_chars])
    if not query_vec:
        return {"error": "Embeddings unavailable.", "matches": []}

    matches = []
    for chunk in await list_chunk_embeddings(db_path, path):
        try:
            chunk_vec = json.loads(str(chunk.get("embedding") or ""))
        except Exception:
            continue
        score = cosine(query_vec, chunk_vec)
        matches.append(
            {
                "chunk_id": chunk.get("chunk_id", ""),
                "chunker_version": chunk.get("chunker_version"),
                "path": chunk.get("path", ""),
                "index": chunk.get("index"),
                "start_char": chunk.get("start_char"),
                "end_char": chunk.get("end_char"),
                "body_start_char": chunk.get("body_start_char"),
                "body_end_char": chunk.get("body_end_char"),
                "heading": chunk.get("heading", ""),
                "body_hash": chunk.get("body_hash", ""),
                "text_hash": chunk.get("text_hash", ""),
                "score": round(score, 4),
                "text": chunk.get("text", ""),
            }
        )

    matches.sort(key=lambda item: -item["score"])
    return {"query": normalized_query, "top_k": limit, "matches": matches[:limit]}


async def list_files(
    db_path: str,
    root: Path,
    *,
    excluded_dirs: set[str],
    subfolder: str = "",
    filter_level: Optional[int] = None,
    filter_sign: str = "",
    no_metadata: bool = False,
    get_file_summaries: GetFileSummaries,
) -> List[Dict[str, Any]]:
    """List Markdown files known to the index, with optional lightweight filters."""
    search_root = root / subfolder if subfolder else root

    all_files = []
    for path in search_root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") or part in excluded_dirs for part in rel_parts):
            continue
        rel = str(path.relative_to(root))
        all_files.append((str(path), rel))

    if not all_files:
        return []

    db_data = await get_file_summaries(db_path, [abs_path for abs_path, _ in all_files])

    files = []
    for abs_path, rel in all_files:
        if abs_path not in db_data:
            if no_metadata:
                files.append({"path": rel, "type": None, "level": None, "sign": None})
            continue
        file_type, level, sign = db_data[abs_path]
        if filter_level is not None and level != filter_level:
            continue
        if filter_sign and filter_sign not in (sign or ""):
            continue
        files.append({
            "path": rel,
            "type": file_type,
            "level": level,
            "sign": sign,
        })

    return files


async def read_file(
    db_path: str,
    file_path: Path,
    *,
    read_file_with_metadata: ReadFile,
    index_file: IndexFile,
    check_parents_exist: CheckParentsExist,
) -> Dict[str, Any]:
    """Read one note, refresh its index row, and add parent warnings."""
    data = await read_file_with_metadata(file_path)
    await index_file(db_path, file_path, data)
    missing = check_parents_exist(data)
    if missing:
        data["warnings"] = [f"parent_missing: {parent}" for parent in missing]
    return data


async def write_file_with_metadata(
    db_path: str,
    root: Path,
    file_path: Path,
    content: str,
    metadata: Dict[str, Any],
    *,
    clean_content: bool = False,
    sync_parents_fields: SyncParentsFields,
    resolve_entity_path: ResolveEntityPath,
    check_cycle_exists: CheckCycleExists,
    dump_metadata: DumpMetadata,
    write_text: WriteText,
    index_file: IndexFile,
    logger: logging.Logger | None = None,
) -> tuple[bool, str]:
    """Write one Markdown file with YAML metadata and refresh its index row."""
    try:
        synced = sync_parents_fields(metadata)

        if db_path and synced.get("parents_meta"):
            rel_path = str(file_path.relative_to(root)) if file_path.is_absolute() else str(file_path)
            for parent in synced["parents_meta"]:
                if isinstance(parent, dict):
                    parent_entity = parent.get("entity", "")
                    if parent_entity:
                        parent_path = await resolve_entity_path(db_path, parent_entity)
                        if parent_path:
                            has_cycle = await check_cycle_exists(db_path, parent_path, rel_path)
                            if has_cycle:
                                if logger:
                                    logger.warning(f"Cycle detected: {parent_entity} -> {rel_path} (skipped)")
                                return False, "cycle_detected"

        yaml_str = dump_metadata(synced)
        body = content.strip() if clean_content else content
        output = f"---\n{yaml_str}\n---\n{body}"
        await write_text(file_path, output)

        if db_path:
            await index_file(db_path, file_path, {**synced, "content": body})

        return True, ""
    except Exception as exc:
        if logger:
            logger.error(f"Error writing to {file_path}: {exc}")
        return False, str(exc)


async def write_file(
    db_path: str,
    file_path: Path,
    rel_path: str,
    content: str,
    metadata: Dict[str, Any],
    *,
    content_lock: bool,
    read_text: ReadText,
    split_frontmatter: SplitFrontmatter,
    write_file_with_metadata: WriteFileWithMetadata,
) -> Dict[str, Any]:
    """Write a note, optionally preserving the existing body text."""
    if content_lock and file_path.exists():
        file_full_text = await read_text(file_path)
        _, content = split_frontmatter(file_full_text)

    success, error = await write_file_with_metadata(file_path, content, metadata, db_path)
    if success:
        return {"status": "ok", "path": rel_path}
    return {"status": "error", "reason": error}


async def update_metadata(
    db_path: str,
    file_path: Path,
    rel_path: str,
    metadata: Dict[str, Any],
    *,
    read_text: ReadText,
    split_frontmatter: SplitFrontmatter,
    write_file_with_metadata: WriteFileWithMetadata,
) -> Dict[str, Any]:
    """Update YAML metadata while preserving the Markdown body exactly."""
    file_full_text = await read_text(file_path)
    _, body = split_frontmatter(file_full_text)

    success, error = await write_file_with_metadata(file_path, body, metadata, db_path)
    if success:
        return {"status": "ok", "path": rel_path, "body_preserved": True}
    return {"status": "error", "reason": error}


async def suggest_parents(
    file_path: str,
    db_path: str,
    *,
    top_n: int = 3,
    embed_max_chars: int,
    core_signs: set[str],
    determine_core: DetermineCore,
    get_embedding: GetEmbedding,
    list_embedding_candidates: ListEmbeddingCandidates,
    parse_frontmatter: ParseFrontmatter,
    cosine: Cosine,
) -> Dict[str, Any]:
    """Suggest likely parent entities for a note based on embedding similarity."""
    full_path = Path(file_path)
    if not full_path.exists():
        return {"error": "File not found", "candidates": []}

    raw = full_path.read_text(encoding="utf-8")
    frontmatter, content = parse_frontmatter(raw)

    if not content:
        return {"error": "Empty content", "candidates": []}

    core_result = await determine_core(content, db_path)
    dominant_core = core_result.get("dominant")
    own_vec = await get_embedding(content[:embed_max_chars])

    if not own_vec:
        return {
            "error": "Embeddings unavailable.",
            "dominant_core": dominant_core,
            "core_scores": core_result.get("scores", {}),
            "candidates": [],
        }

    current_level = 5
    try:
        current_level = int(frontmatter.get("level", 5))
    except (TypeError, ValueError):
        current_level = 5

    target_parent_level = current_level - 1 if current_level and current_level > 1 else None
    candidates = []
    rows = await list_embedding_candidates(db_path)

    for path, file_type, level, sign, emb_json in rows:
        if path == file_path:
            continue
        if current_level and level is not None and level >= current_level:
            continue
        try:
            other_vec = json.loads(emb_json)
        except Exception:
            continue

        sim = cosine(own_vec, other_vec)
        entity_core = ""
        for char in (sign or ""):
            if char in core_signs:
                entity_core = char
                break

        same_core = entity_core == dominant_core if dominant_core else False
        rank_score = sim
        if same_core:
            rank_score += 0.03
        if target_parent_level is not None and level == target_parent_level:
            rank_score += 0.02

        candidates.append({
            "path": path,
            "type": file_type,
            "level": level,
            "sign": sign,
            "core": entity_core or "?",
            "same_core": same_core,
            "score": round(sim, 3),
            "rank_score": round(rank_score, 3),
        })

    candidates.sort(key=lambda item: -item["rank_score"])
    top_candidates = candidates[:top_n]

    for candidate in top_candidates:
        candidate["recommended_link_type"] = "hierarchy"

    return {
        "dominant_core": dominant_core,
        "core_scores": core_result.get("scores", {}),
        "spread": core_result.get("spread", 0),
        "candidates": top_candidates,
    }


async def suggest_metadata(
    content: str,
    context: Dict[str, Any],
    db_path: str,
    file_path: str = "",
    *,
    reference_vectors: bool,
    semantic_bridges_enabled: bool,
    core_mix_enabled: bool,
    has_sign_auto: bool,
    core_signs: set[str],
    get_parents_meta: GetParentsMeta,
    determine_type: DetermineType,
    embedding_is_fresh: EmbeddingIsFresh,
    get_embedding: GetEmbedding,
    save_embedding: SaveEmbedding,
    load_embedding: LoadEmbedding,
    determine_sign: DetermineSign,
    get_level_from_meta: GetLevelFromMeta,
    find_semantic_bridges: FindSemanticBridges,
    check_hierarchy: CheckHierarchy,
    resolve_entity_path: ResolveEntityPath,
    check_cycle_exists: CheckCycleExists,
    determine_core: DetermineCore,
    get_core_mix: GetCoreMix,
) -> Dict[str, Any]:
    """Suggest metadata, bridge candidates, and drift signals for one note."""
    meta = context or {}
    if file_path and not meta.get("path"):
        meta["path"] = file_path

    type_ = determine_type(content, meta)
    parents_obj = get_parents_meta(meta)

    tags = explicit_tag_list(meta)

    semantic_bridges = []
    vec = []
    if file_path and reference_vectors:
        if not await embedding_is_fresh(db_path, file_path):
            vec = await get_embedding(content)
            if vec:
                await save_embedding(db_path, file_path, vec)
        else:
            vec = await load_embedding(db_path, file_path)

    sign_result = await determine_sign(content, meta, db_path, get_level_from_meta(meta))
    actual_sign = sign_result["actual_sign"]
    sign_auto = sign_result["sign_auto"]
    sign_source = sign_result["source"]
    artifact_sign = sign_result.get("artifact_sign", "")

    if vec and file_path and semantic_bridges_enabled:
        semantic_bridges = await find_semantic_bridges(
            db_path, str(file_path), actual_sign, vec, sign_source
        )

    errors = check_hierarchy(type_, parents_obj)

    if parents_obj and file_path:
        for parent in parents_obj:
            if isinstance(parent, dict) and parent.get("link_type", "hierarchy") == "hierarchy":
                parent_entity = parent.get("entity", "")
                if parent_entity:
                    parent_path = await resolve_entity_path(db_path, parent_entity)
                    if parent_path:
                        has_cycle = await check_cycle_exists(db_path, parent_path, str(file_path))
                        if has_cycle:
                            errors.append({
                                "type": "cycle_error",
                                "entity": parent_entity,
                                "message": f"Creating parent link to '{parent_entity}' would create a cycle in the graph",
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
            {**bridge, "proposed": True} for bridge in semantic_bridges
        ],
        "tag_bridges": [],
    }

    if content and reference_vectors:
        core_result = await determine_core(content, db_path)
        if core_result.get("percentages"):
            result["core_percentages"] = core_result["percentages"]
        result["max_cosine"] = core_result.get("max_cosine", 0.0)
        result["confident"] = core_result.get("confident", False)

    warnings = []
    metrics = {}
    stored_mix = None

    if file_path and core_mix_enabled:
        try:
            stored_mix = await get_core_mix(db_path, file_path)
            if stored_mix:
                leading_core = max(stored_mix, key=lambda key: stored_mix[key])
                own_sign_cores = [char for char in actual_sign if char in core_signs]
                if own_sign_cores and leading_core != own_sign_cores[0]:
                    pct = stored_mix.get(leading_core, 0)
                    entity_name = meta.get("type", "entity")
                    warnings.append({
                        "type": "core_drift",
                        "message": f"{entity_name} sign={actual_sign!r} (Intent), but content is predominantly {leading_core} ({pct}%) -- drift between intent and reality.",
                    })
        except Exception:
            stored_mix = None

    if meta.get("sign") and has_sign_auto:
        metrics["drift_manual_vs_auto"] = (str(meta.get("sign")) != sign_auto)

    if file_path and core_mix_enabled:
        if stored_mix:
            leading_core = max(stored_mix, key=lambda key: stored_mix[key])
            auto_cores = [char for char in sign_auto if char in core_signs]
            metrics["drift_auto_vs_core"] = (auto_cores and leading_core != auto_cores[0])
        else:
            metrics["drift_auto_vs_core"] = False

    if metrics:
        result["metrics"] = metrics

    if warnings:
        result["warnings"] = warnings

    return result


async def recalc_signs(
    db_path: str,
    *,
    dry_run: bool,
    has_sign_auto: bool,
    mode_name: str,
    core_mix_enabled: bool,
    core_signs: set[str],
    is_meta_root: Callable[[str], bool],
    list_sign_rows: ListSignRecalcRows,
    determine_artifact_sign: DetermineArtifactSign,
    determine_core: DetermineCore,
    read_artifact_sign: ReadArtifactSign,
    update_sign_rows: UpdateSignRecalcRows,
) -> Dict[str, Any]:
    """Reclassify indexed notes and optionally persist automatic signs."""
    if not has_sign_auto:
        return {
            "error": f"This tool is not available in '{mode_name}' mode. Use 'prizma' or 'sloi' mode for semantic classification."
        }

    updated = 0
    rows = await list_sign_rows(db_path)
    sign_updates: List[tuple[str, str, str, str, str]] = []
    mix_updates: List[tuple[str, str]] = []

    for path, content, level in rows:
        if not content:
            continue
        if is_meta_root(path):
            continue
        if level in (0, 1):
            continue

        if level == 5:
            artifact_sign = determine_artifact_sign(content, {})
            sign_updates.append((artifact_sign, "auto", artifact_sign, artifact_sign, path))
        else:
            core_result = await determine_core(content, db_path)
            above = core_result.get("above_threshold", [])
            sign_auto = "".join(above) if above else (list(core_signs)[0] if core_signs else "")
            confident = core_result.get("confident", False)
            source = "auto" if confident else "weak_auto"
            percentages = core_result.get("percentages", {})

            artifact_sign = ""
            if level == 4:
                artifact_sign = read_artifact_sign(path)

            composite = artifact_sign + sign_auto if artifact_sign else sign_auto
            sign_updates.append((composite, source, sign_auto, artifact_sign, path))

            if percentages and core_mix_enabled:
                mix_updates.append((json.dumps(percentages), path))

        updated += 1

    if not dry_run and sign_updates:
        await update_sign_rows(db_path, sign_updates, mix_updates)

    return {"updated": updated, "dry_run": dry_run}


async def recalc_core_mix(
    db_path: str,
    *,
    core_mix_enabled: bool,
    mode_name: str,
    level_strict: bool,
    list_file_levels: ListFileLevels,
    is_meta_root: IsMetaRoot,
    aggregate_core_mix: AggregateCoreMix,
    update_core_mixes: UpdateCoreMixes,
) -> Dict[str, Any]:
    """Recalculate stored core_mix values for indexed non-artifact entities."""
    if not core_mix_enabled:
        return {"error": f"This tool is not available in '{mode_name}' mode."}

    rows = await list_file_levels(db_path)

    updates = []
    for path, level in rows:
        if is_meta_root(Path(path)):
            continue
        if level == 5:
            continue
        if level_strict:
            child_level = (level or 0) - 1 if level else None
        else:
            child_level = None
        core_mix = await aggregate_core_mix(db_path, path, child_level)
        if core_mix:
            updates.append((json.dumps(core_mix), path))

    await update_core_mixes(db_path, updates)
    return {"updated": len(updates)}


async def _suggest_auto_parent_links(
    content: str,
    db_path: str,
    file_path: str,
    level: int,
    *,
    actual_sign: str = "",
    prefer_actual_sign_core: bool = False,
    reference_vectors: bool,
    embed_max_chars: int,
    parent_link_threshold: float,
    core_signs: set[str],
    get_embedding: GetEmbedding,
    determine_core: DetermineCore,
    list_parent_candidates: ListParentEmbeddingCandidates,
    find_temporary_anchor: FindTemporaryAnchor,
    cosine: Cosine,
) -> List[Dict[str, str]]:
    """Suggest hierarchy/temporary parents for a note-like entity."""
    parents_meta: List[Dict[str, str]] = []

    if reference_vectors:
        vec = await get_embedding(content[:embed_max_chars])
        if vec:
            rows = await list_parent_candidates(db_path, file_path, level)
            core_result = await determine_core(content, db_path)

            if prefer_actual_sign_core and actual_sign:
                sign_cores = [char for char in actual_sign if char in core_signs]
                dominant_core = sign_cores[0] if sign_cores else core_result.get("dominant")
            else:
                dominant_core = core_result.get("dominant")

            best = None
            best_score = 0.0
            best_same_core = False
            for parent_path, parent_sign, _parent_level, emb_json in rows:
                try:
                    other_vec = json.loads(emb_json)
                except Exception:
                    continue
                sim = cosine(vec, other_vec)
                parent_cores = [char for char in (parent_sign or "") if char in core_signs]
                same_core = dominant_core and dominant_core in parent_cores if parent_cores else False
                if sim > best_score or (sim == best_score and same_core and not best_same_core):
                    best_score = sim
                    best_same_core = same_core
                    best = Path(parent_path).stem

            if best and best_score >= parent_link_threshold:
                parents_meta.append({"entity": best, "link_type": "hierarchy"})

    anchor = await find_temporary_anchor(content, db_path, level)
    if anchor and not any(parent.get("entity") == anchor for parent in parents_meta):
        parents_meta.append({"entity": anchor, "link_type": "temporary"})

    return parents_meta


async def process_orphans(
    db_path: str,
    root: Path,
    *,
    dry_run: bool = False,
    auto_parents: bool = True,
    limit: int = 50,
    has_sign_auto: bool,
    reference_vectors: bool,
    embed_max_chars: int,
    parent_link_threshold: float,
    core_signs: set[str],
    excluded_dirs: set[str],
    is_meta_root: IsMetaRoot,
    parse_frontmatter: ParseFrontmatter,
    get_level_from_meta: GetLevelFromMeta,
    determine_sign: DetermineSign,
    get_parents_meta: GetParentsMeta,
    get_embedding: GetEmbedding,
    determine_core: DetermineCore,
    list_parent_candidates: ListParentEmbeddingCandidates,
    find_temporary_anchor: FindTemporaryAnchor,
    cosine: Cosine,
    write_file: WriteFileWithMetadata,
) -> Dict[str, Any]:
    """Find files with missing sign/parents and optionally write suggested metadata."""
    if not has_sign_auto:
        return {"error": "Not available in luca mode"}

    orphans = []
    for path in root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") or part in excluded_dirs for part in rel_parts):
            continue
        if is_meta_root(path):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            frontmatter, content = parse_frontmatter(raw)
            sign = str(frontmatter.get("sign", "")).strip()
            level = frontmatter.get("level", 0)
            if isinstance(level, str):
                level = int(level) if level.isdigit() else 0
            has_parents = bool(frontmatter.get("parents") or frontmatter.get("parents_meta"))
            if not sign:
                orphans.append((path, frontmatter, content, "no_sign"))
            elif level >= 2 and not has_parents:
                orphans.append((path, frontmatter, content, "no_parents"))
        except Exception:
            continue
        if len(orphans) >= limit:
            break

    if not orphans:
        return {"processed": 0, "orphans": []}

    results = []
    for path, frontmatter, content, reason in orphans:
        rel = str(path.relative_to(root))
        if not content:
            content = ""

        level = get_level_from_meta(frontmatter)

        if reason == "no_sign":
            sign_result = await determine_sign(
                content,
                {**frontmatter, "path": str(path)},
                db_path,
                level,
            )
        else:
            sign_result = {
                "actual_sign": frontmatter.get("sign", ""),
                "source": "existing",
                "artifact_sign": frontmatter.get("artifact_sign", ""),
            }

        tags = explicit_tag_list(frontmatter)

        parents_meta = get_parents_meta(frontmatter)
        parents_auto = False
        if not parents_meta and auto_parents and content:
            parents_meta = await _suggest_auto_parent_links(
                content,
                db_path,
                str(path),
                level,
                actual_sign=sign_result.get("actual_sign", ""),
                prefer_actual_sign_core=(reason != "no_sign"),
                reference_vectors=reference_vectors,
                embed_max_chars=embed_max_chars,
                parent_link_threshold=parent_link_threshold,
                core_signs=core_signs,
                get_embedding=get_embedding,
                determine_core=determine_core,
                list_parent_candidates=list_parent_candidates,
                find_temporary_anchor=find_temporary_anchor,
                cosine=cosine,
            )
            parents_auto = bool(parents_meta)

        entry = {
            "path": rel,
            "level": level,
            "sign": sign_result["actual_sign"],
            "artifact_sign": sign_result.get("artifact_sign", ""),
            "sign_source": sign_result.get("source", ""),
            "tags": tags,
            "hierarchy_parents": [
                parent["entity"] for parent in parents_meta if parent.get("link_type") == "hierarchy"
            ],
            "temporary_parents": [
                parent["entity"] for parent in parents_meta if parent.get("link_type") == "temporary"
            ],
            "parents_auto": parents_auto,
        }

        if not dry_run:
            metadata = {
                **frontmatter,
                "level": level,
                "sign": sign_result["actual_sign"],
                "parents_meta": parents_meta,
            }
            if tags or "tags" in frontmatter:
                metadata["tags"] = tags
            if sign_result.get("artifact_sign"):
                metadata["artifact_sign"] = sign_result["artifact_sign"]
            success, error = await write_file(path, content, metadata, db_path)
            entry["status"] = "ok" if success else f"error: {error}"
        else:
            entry["status"] = "preview"

        results.append(entry)

    return {"processed": len(results), "dry_run": dry_run, "orphans": results}


async def add_entity(
    db_path: str,
    file_path: Path,
    rel_path: str,
    content: str,
    *,
    level: Any = 5,
    entity_type: str = "",
    manual_sign: str = "",
    explicit_parents: List[Any] | None = None,
    explicit_tags: List[str] | None = None,
    auto_parents: bool = True,
    has_sign_auto: bool,
    reference_vectors: bool,
    embed_max_chars: int,
    parent_link_threshold: float,
    core_signs: set[str],
    get_type_by_level: GetTypeByLevel,
    determine_sign: DetermineSign,
    get_embedding: GetEmbedding,
    determine_core: DetermineCore,
    list_parent_candidates: ListParentEmbeddingCandidates,
    find_temporary_anchor: FindTemporaryAnchor,
    cosine: Cosine,
    write_file: WriteFileWithMetadata,
) -> Dict[str, Any]:
    """Create one entity file with suggested sign and optional parent links."""
    if not has_sign_auto:
        return {"error": "Not available in luca mode. Use write_file instead."}

    if not isinstance(level, int) or level < 1 or level > 5:
        level = 5

    explicit_parents = explicit_parents or []
    explicit_tags = explicit_tags or []
    entity_type = entity_type or get_type_by_level(level)

    metadata: Dict[str, Any] = {"level": level, "type": entity_type, "path": str(file_path)}
    if manual_sign:
        metadata["sign"] = manual_sign

    sign_result = await determine_sign(content, metadata, db_path, level)
    metadata["sign"] = sign_result["actual_sign"]
    if sign_result.get("artifact_sign"):
        metadata["artifact_sign"] = sign_result["artifact_sign"]

    tags = explicit_tag_list({"tags": explicit_tags})
    if tags:
        metadata["tags"] = tags

    if explicit_parents:
        metadata["parents_meta"] = explicit_parents
        metadata["parents"] = [
            parent.get("entity", "") if isinstance(parent, dict) else str(parent)
            for parent in explicit_parents
        ]
    elif auto_parents and content:
        parents_meta = await _suggest_auto_parent_links(
            content,
            db_path,
            str(file_path),
            level,
            reference_vectors=reference_vectors,
            embed_max_chars=embed_max_chars,
            parent_link_threshold=parent_link_threshold,
            core_signs=core_signs,
            get_embedding=get_embedding,
            determine_core=determine_core,
            list_parent_candidates=list_parent_candidates,
            find_temporary_anchor=find_temporary_anchor,
            cosine=cosine,
        )
        if parents_meta:
            metadata["parents_meta"] = parents_meta
            metadata["parents"] = [parent["entity"] for parent in parents_meta]

    success, error = await write_file(file_path, content, metadata, db_path)
    if not success:
        return {"status": "error", "reason": error}

    parents_meta_result = metadata.get("parents_meta", [])
    hierarchy_parents = [
        parent["entity"]
        for parent in parents_meta_result
        if isinstance(parent, dict) and parent.get("link_type") == "hierarchy"
    ]
    temporary_parents = [
        parent["entity"]
        for parent in parents_meta_result
        if isinstance(parent, dict) and parent.get("link_type") == "temporary"
    ]
    return {
        "status": "created",
        "path": rel_path,
        "level": level,
        "sign": metadata.get("sign", ""),
        "artifact_sign": metadata.get("artifact_sign", ""),
        "sign_source": sign_result.get("source", ""),
        "tags": tags,
        "hierarchy_parents": hierarchy_parents,
        "temporary_parents": temporary_parents,
    }
