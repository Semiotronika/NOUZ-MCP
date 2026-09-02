"""Sign and artifact-sign helpers for NOUZ."""

from typing import Any, Dict, Iterable, List, Mapping, Set

# Local configurations may name artifact kinds in Russian while the public
# heuristic uses stable English keys. Resolve local aliases before fallback.
_ARTIFACT_NAME_ALIASES = {
    "note": ("\u0437\u0430\u043c\u0435\u0442\u043a\u0430",),
    "concept": ("\u043f\u043e\u043d\u044f\u0442\u0438\u0435",),
    "reference": ("\u0440\u0435\u0444\u0435\u0440\u0435\u043d\u0441", "\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a"),
    "log": ("\u043b\u043e\u0433",),
    "update": ("\u043d\u043e\u0432\u043e\u0441\u0442\u044c", "\u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435"),
    "hypothesis": ("\u0433\u0438\u043f\u043e\u0442\u0435\u0437\u0430",),
    "specification": ("\u0441\u043f\u0435\u0446\u0438\u0444\u0438\u043a\u0430\u0446\u0438\u044f",),
}


def _mapping_value(name: str, mapping: Mapping[str, Any]) -> Any:
    """Find a configured artifact value by English or local alias."""
    candidates = {name.strip().lower().replace("\u0451", "\u0435")}
    candidates.update(
        alias.lower().replace("\u0451", "\u0435")
        for alias in _ARTIFACT_NAME_ALIASES.get(name.strip().lower(), ())
    )
    for key, value in mapping.items():
        normalized = str(key).strip().lower().replace("\u0451", "\u0435")
        if normalized in candidates:
            return value
    return None


def extract_artifact_sign_from_sign(sign: str, artifact_signs: Set[str]) -> str:
    """Extract artifact-sign characters from a composite sign."""
    return "".join(ch for ch in sign if ch in artifact_signs)


def dedupe_sign_chars(sign: str) -> str:
    """Dedupe sign characters while preserving their original order."""
    result: List[str] = []
    for ch in sign or "":
        if ch not in result:
            result.append(ch)
    return "".join(result)


def extract_core_sign_from_sign(sign: str, core_signs: Set[str], artifact_signs: Set[str]) -> str:
    """Extract core/domain sign characters from a composite sign."""
    if core_signs:
        return "".join(ch for ch in sign if ch in core_signs)
    return "".join(ch for ch in sign if ch not in artifact_signs)


def artifact_sign(name: str, fallback: str, artifact_sign_by_name: Mapping[str, str]) -> str:
    """Return configured artifact sign by material name, with public ASCII fallback."""
    value = _mapping_value(name, artifact_sign_by_name)
    if value is not None:
        return str(value)
    if name.strip().lower() == "update":
        value = _mapping_value("news", artifact_sign_by_name)
        if value is not None:
            return str(value)
    return fallback


def artifact_keywords(
    name: str,
    artifact_keywords_by_name: Mapping[str, Iterable[str]],
    default_artifact_keywords: Mapping[str, Iterable[str]],
) -> List[str]:
    """Return configured artifact detection keywords, or public RU/EN defaults."""
    value = _mapping_value(name, artifact_keywords_by_name)
    if value is not None:
        return list(value)
    if name.strip().lower() == "update":
        value = _mapping_value("news", artifact_keywords_by_name)
        if value is not None:
            return list(value)
    return list(default_artifact_keywords.get(name.lower(), []))


def determine_artifact_sign(
    content: str,
    meta: Dict[str, Any],
    artifact_sign_by_name: Mapping[str, str],
    artifact_keywords_by_name: Mapping[str, Iterable[str]],
    default_artifact_keywords: Mapping[str, Iterable[str]],
) -> str:
    """Determine artifact sign by content structure/heuristics; no embeddings needed."""
    del meta  # Reserved for future metadata-aware rules.

    def configured_sign(name: str, fallback: str) -> str:
        return artifact_sign(name, fallback, artifact_sign_by_name)

    def configured_keywords(name: str) -> List[str]:
        return artifact_keywords(name, artifact_keywords_by_name, default_artifact_keywords)

    if not content:
        return configured_sign("note", "n")

    text = content.lower()

    if any(kw in text for kw in configured_keywords("specification")):
        return configured_sign("specification", "s")
    if any(kw in text for kw in configured_keywords("log")):
        return configured_sign("log", "l")
    if any(kw in text for kw in configured_keywords("update")):
        return configured_sign("update", "u")
    if any(kw in text for kw in configured_keywords("hypothesis")):
        return configured_sign("hypothesis", "h")
    if any(kw in text for kw in configured_keywords("reference")):
        return configured_sign("reference", "r")
    if any(kw in text for kw in configured_keywords("concept")):
        return configured_sign("concept", "c")

    return configured_sign("note", "n")


def signs_share_core(sign_a: str, sign_b: str, core_signs: Set[str]) -> bool:
    """Return True when two signs share at least one configured core sign."""
    if not sign_a or not sign_b:
        return False
    for ch in sign_a:
        if ch in core_signs and ch in sign_b:
            return True
    return False
