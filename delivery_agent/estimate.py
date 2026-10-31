"""Estimate parsing helpers. The estimate rules themselves are human-written (config/rules.md)."""
from __future__ import annotations

import re


_UNIT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(h|hr|hrs|hour|hours|d|day|days|md|w|wk|week|weeks|m|min|mins|minutes)?\s*$", re.I)


def parse_hours(value, hours_per_day: float = 8, hours_per_week: float = 40) -> float | None:
    """'24h' -> 24, '3d' -> 24, '1w' -> 40, '90m' -> 1.5, '6' -> 6. Returns None if empty/unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _UNIT_RE.match(str(value))
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "h").lower()
    if unit in ("d", "day", "days", "md"):
        return number * hours_per_day
    if unit in ("w", "wk", "week", "weeks"):
        return number * hours_per_week
    if unit in ("m", "min", "mins", "minutes"):
        return number / 60
    return number


def fmt_hours(hours: float) -> str:
    return f"{hours:g}h"
