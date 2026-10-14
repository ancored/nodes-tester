"""Срок годности результата теста или проверки.

Результат живёт ttl, укороченный на долю ttl_jitter. Доля случайна, но постоянна для пары
«ключ, время замера»: тестер, рейтинг и админка считают один и тот же срок, ничего не
храня. Так сроки нод одного хоста, проверенных подряд, со временем расходятся.
"""

from __future__ import annotations

import math
import time
import zlib


def expires(ts, ttl: float, jitter: float, key: str) -> float:
    """Когда истекает результат, полученный в ts; ttl <= 0 — бессрочно, нет ts — уже истёк."""
    if not ts:
        return 0.0
    if ttl <= 0:
        return math.inf
    share = zlib.crc32(f"{key}:{int(ts)}".encode()) / 2 ** 32
    return int(ts) + ttl - min(jitter, ttl) * share


def fresh(ts, ttl: float, jitter: float, key: str) -> bool:
    return time.time() < expires(ts, ttl, jitter, key)
