"""Sign and artifact-sign helpers for NOUZ."""

from typing import Any, Dict, Iterable, List, Mapping, Set


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
    key = name.lower()
    if key in artifact_sign_by_name:
        return artifact_sign_by_name[key]
    if key == "update" and "news" in artifact_sign_by_name:
        return artifact_sign_by_name["news"]
    return fallback


def artifact_keywords(
    name: str,
    artifact_keywords_by_name: Mapping[str, Iterable[str]],
    default_artifact_keywords: Mapping[str, Iterable[str]],
) -> List[str]:
    """Return configured artifact detection keywords, or public RU/EN defaults."""
    key = name.lower()
    if key in artifact_keywords_by_name:
        return list(artifact_keywords_by_name[key])
    if key == "update" and "news" in artifact_keywords_by_name:
        return list(artifact_keywords_by_name["news"])
    return list(default_artifact_keywords.get(key, []))


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
