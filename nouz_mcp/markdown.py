"""Markdown frontmatter and metadata helpers."""

import re
from typing import Any, Dict

import yaml


YAML_ALLOWED_KEYS = {'type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta'}
KEY_ORDER = ['type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta']


def parse_frontmatter(raw: str) -> tuple[Dict[str, Any], str]:
    """Parse Markdown YAML frontmatter without treating plain horizontal rules as YAML."""
    raw_for_parse = raw[1:] if raw.startswith("\ufeff") else raw
    if not raw_for_parse.startswith("---"):
        return {}, raw

    lines = raw_for_parse.splitlines(keepends=True)
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


def split_frontmatter_raw(raw: str) -> tuple[Dict[str, Any], str]:
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


def _has_yaml_value(value: Any) -> bool:
    if value is None or value == []:
        return False
    if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
        return False
    return True


def _yaml_str(s: str) -> str:
    if any(c in s for c in ':#{}[]|>&*!,\'\"\\'):
        escaped = s.replace("'", "''")
        return f"'{escaped}'"
    return s


def dump_metadata(metadata: Dict[str, Any]) -> str:
    # Whitelist: only these keys are written to YAML.
    # Internal fields (sign_source, sign_auto, color, core_mix, path, content, etc.) are not included.
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


def sync_parents_fields(metadata: Dict[str, Any]) -> Dict[str, Any]:
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
