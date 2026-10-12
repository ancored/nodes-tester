"""Длительности в конфиге — строками, как у sing-box: "250ms", "5s", "1m30s", "3h", "30d"."""

from __future__ import annotations

import re

_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")
_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """Длительность в секундах. ValueError — не строка вида "1h30m"."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"ожидается длительность строкой, например \"5m\", получено {text!r}")
    value = text.strip()
    parts = _PART.findall(value)
    if not parts or "".join(n + u for n, u in parts) != value:
        raise ValueError(f"{text!r} — не длительность (примеры: \"250ms\", \"5s\", \"1h30m\", \"30d\")")
    return sum(float(number) * _SECONDS[unit] for number, unit in parts)
