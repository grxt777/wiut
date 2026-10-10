from datetime import datetime
from typing import Optional


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamps like '2026-09-16T10:09:20.000Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def get_path(d: dict, *path, default=None):
    """Safe nested dict getter: get_path(item, 'model', 'vendor', 'name')."""
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default
