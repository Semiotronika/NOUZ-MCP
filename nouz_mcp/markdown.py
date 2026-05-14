"""Markdown frontmatter and metadata helpers."""

import re
import unicodedata
from typing import Any, Dict

import yaml


YAML_ALLOWED_KEYS = {'type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta'}
KEY_ORDER = ['type', 'level', 'sign', 'artifact_sign', 'status', 'tags', 'parents', 'parents_meta']
TAG_HEX_COLOR_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
TAG_URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
TAG_MAX_LENGTH = 64


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


def canonical_tag(value: Any) -> str:
    """Return the canonical form used for YAML tags and tag bridges."""
    tag, _reason = _canonical_tag_with_reason(value)
    return tag


def explicit_tag_report(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return canonical YAML tags plus discarded raw values with reason codes."""
    raw = metadata.get("tags", [])
    if raw in (None, "", "None", "none", "NULL", "null"):
        return {"tags": [], "dropped": []}

    values = raw if isinstance(raw, list) else [raw]
    tags: list[str] = []
    dropped: list[Dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        tag, reason = _canonical_tag_with_reason(value)
        if not tag:
            if reason not in {"empty", "placeholder"}:
                dropped.append({"value": _tag_preview(value), "reason": reason})
            continue
        if tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return {"tags": tags, "dropped": dropped}


def _canonical_tag_with_reason(value: Any) -> tuple[str, str]:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return "", "invalid_type"

    raw = unicodedata.normalize("NFKC", str(value)).strip()
    if not raw:
        return "", "empty"

    stripped = raw.lstrip("#").strip().lower()
    if stripped in {"", "none", "null"}:
        return "", "placeholder"
    if TAG_URL_RE.match(stripped) or "://" in stripped:
        return "", "url"
    if (raw.startswith("#") and TAG_HEX_COLOR_RE.fullmatch(raw)) or (
        len(stripped) in {6, 8}
        and any(ch.isdigit() for ch in stripped)
        and TAG_HEX_COLOR_RE.fullmatch(stripped)
    ):
        return "", "hex_color"

    tag = _slugify_tag(raw.lstrip("#").strip().lower())
    if not tag:
        return "", "no_letters"
    if len(tag) > TAG_MAX_LENGTH:
        return "", "too_long"
    if not any(ch.isalpha() for ch in tag):
        return "", "no_letters"
    if sum(1 for ch in tag if ch.isalnum()) < 2:
        return "", "too_short"
    return tag, ""


def _slugify_tag(value: str) -> str:
    segments = []
    for raw_segment in value.replace("\\", "/").split("/"):
        segment = _slugify_tag_segment(raw_segment)
        if segment:
            segments.append(segment)
    return "/".join(segments)


def _slugify_tag_segment(value: str) -> str:
    chars: list[str] = []
    last_was_separator = False
    for ch in value:
        if ch.isalnum():
            if last_was_separator and chars:
                chars.append("-")
            chars.append(ch)
            last_was_separator = False
        else:
            last_was_separator = True
    return re.sub(r"-{2,}", "-", "".join(chars)).strip("-")


def _tag_preview(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
    else:
        text = type(value).__name__
    return text[:80]


def explicit_tag_list(metadata: Dict[str, Any]) -> list[str]:
    """Return canonical YAML tags that were explicitly provided in metadata."""
    return explicit_tag_report(metadata)["tags"]


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

    if "tags" in ordered:
        tags = explicit_tag_list({"tags": ordered["tags"]})
        if tags:
            ordered["tags"] = tags
        else:
            del ordered["tags"]

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
