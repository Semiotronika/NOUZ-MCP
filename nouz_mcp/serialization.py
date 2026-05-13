"""Serialization helpers for MCP responses and YAML metadata."""

from datetime import date, datetime
from typing import Any


def serialize(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj
