"""Configuration defaults and loading for NOUZ."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml


DEFAULT_ARTIFACT_SIGNS = [
    {"sign": "n", "name": "Note", "text": "Short note, observation, fragment."},
    {"sign": "c", "name": "Concept", "text": "Definition, concept, entity description."},
    {"sign": "r", "name": "Reference", "text": "External source, documentation, link, citation."},
    {"sign": "l", "name": "Log", "text": "Session log, chronology, dialogue record."},
    {"sign": "u", "name": "Update", "text": "Update, release note, changelog entry."},
    {"sign": "h", "name": "Hypothesis", "text": "Hypothesis, assumption, speculative idea."},
    {"sign": "s", "name": "Specification", "text": "Technical specification, instruction, requirements."},
]

DEFAULT_ARTIFACT_KEYWORDS = {
    "specification": [
        "должно быть", "требования", "спецификац", "инструкц", "архитектурн",
        "техническое задани", "технического задани", "техническим задани",
        "техническому задани", "техзадан", "тз:",
        "must be", "requirements", "specification",
    ],
    "log": [
        "лог ", "сессия", "сначала", "потом", "далее,", "хронолог",
        "что сделали", "что получилось", "что не получилось",
        "session log", "chronology", "timeline", "step by step",
    ],
    "update": [
        "новость", "обновлен", "свеж", "произошло", "что нового",
        "стало известно", "вышло", "релиз", "news:", "update:", "released",
    ],
    "hypothesis": [
        "гипотез", "предположим", "может быть", "возможно,", "спекуляц",
        "допущен", "предположен", "если бы", "что если", "hypothesis",
        "speculation", "what if", "suppose that",
    ],
    "reference": [
        "http://", "https://", "www.", "документац", "сторонн", "ссылк",
        "внешн", "обзор ", "каталог", "reference:", "documentation",
    ],
    "concept": [
        "поняти", "определен", "концепт", "сущност", "это когда",
        "это такой", "это то,", "границы понятия", "свойства",
        "отличия от", "definition", "concept:", "entity",
    ],
}

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
        "confident_spread": 60.0,
        "pattern_second_sign_threshold": 30.0,
        "semantic_bridge_threshold": 0.55,
        "parent_link_threshold": 0.55
    }
}


def apply_profile(config: Dict[str, Any], profile_name: str, source: Path) -> Dict[str, Any]:
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
            return apply_profile(config, profile_name, candidate)
        except Exception as e:
            logging.warning(f"Failed to load config from {candidate}: {e}")

    logging.info("No config.yaml found; using LUCA defaults. Copy config.template.yaml to config.yaml to enable PRIZMA/SLOI.")
    return DEFAULT_CONFIG
