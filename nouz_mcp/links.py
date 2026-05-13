"""Parent-link normalization helpers for NOUZ metadata."""

from pathlib import Path
from typing import Any, Dict, List


def _normalize_parent_entity(entity: str) -> str:
    entity = entity.strip()
    if entity.startswith("[[") and entity.endswith("]]"):
        entity = entity[2:-2]
    return entity


def get_parents_meta(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return structured parent links from parents_meta or parents."""
    parents_meta = meta.get("parents_meta", [])
    if parents_meta and isinstance(parents_meta, list):
        result = []
        for parent in parents_meta:
            if isinstance(parent, dict):
                result.append(parent)
            elif isinstance(parent, str):
                entity = _normalize_parent_entity(parent)
                if entity:
                    result.append({"entity": entity, "link_type": "hierarchy"})
        if result:
            return result

    parents_raw = meta.get("parents", [])
    if parents_raw and isinstance(parents_raw, list):
        result = []
        for parent in parents_raw:
            if isinstance(parent, dict):
                result.append(parent)
            elif isinstance(parent, str):
                entity = _normalize_parent_entity(parent)
                if entity:
                    result.append({"entity": entity, "link_type": "hierarchy"})
        return result

    return []


def check_parents_exist(root: str, meta: Dict[str, Any]) -> List[str]:
    """Return parent entity names that do not have a Markdown file under root."""
    parents = get_parents_meta(meta)
    if not parents:
        return []

    root_path = Path(root)
    missing = []
    for parent in parents:
        entity = parent.get("entity", "") if isinstance(parent, dict) else str(parent)
        if not entity:
            continue
        found = False
        for _ in root_path.rglob(entity + ".md"):
            found = True
            break
        if not found:
            missing.append(entity)
    return missing
