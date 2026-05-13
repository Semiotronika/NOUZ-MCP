"""Mode and level policies for NOUZ."""

from typing import Callable, Dict, List


HierarchyCheck = Callable[[str, List[Dict]], List[Dict]]

LEVEL_TO_TYPE = {
    0: "meta",
    1: "core",
    2: "pattern",
    3: "module",
    4: "quant",
    5: "artifact",
}


def get_type_by_level(level: int) -> str:
    return LEVEL_TO_TYPE.get(level, "artifact")


def get_level(type_str: str, level_map: Dict[str, int]) -> int:
    return level_map.get(type_str, 0)


def build_rules(strict_hierarchy_check: HierarchyCheck) -> Dict[str, Dict]:
    return {
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
            "hierarchy_check": strict_hierarchy_check,
        },
    }
