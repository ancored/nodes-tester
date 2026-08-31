"""Расчёт рейтинга ноды из результатов тестов (stability-first, под РФ).

Модель (обсуждали): метрики нормализуются в [0..1], взвешиваются с упором на
стабильность (~80%), GATE-условия обнуляют мгновенный скор. Временнáя
агрегация — EWMA по прогонам + штраф за флаппинг + множитель доступности.

Всё чистые функции: на вход результаты тестов + пороги/веса из конфига,
на выход — числа. Отсутствующие тесты просто исключаются из расчёта.
"""

from __future__ import annotations

from typing import Optional

# Компоненты рейтинга и их метки (порядок = порядок в score.csv).
COMPONENTS = ("reliability", "consistency", "throttle", "jitter", "latency", "throughput")


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def lower_better(x: Optional[float], good: float, bad: float) -> Optional[float]:
    if x is None:
        return None
    if bad == good:
        return 1.0
    return clamp01((bad - x) / (bad - good))


def higher_better(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    if x is None:
        return None
    if hi == lo:
        return 1.0
    return clamp01((x - lo) / (hi - lo))


def gate_ok(tests: dict, th: dict) -> bool:
    """Жёсткие условия: их провал обнуляет мгновенный скор."""
    conn = tests.get("connectivity")
    if conn is not None and not conn.get("ok"):
        return False
    jit = tests.get("jitter")
    if jit is not None and jit.get("loss_pct", 0) > th["loss_gate"]:
        return False
    reach = tests.get("reachability")
    if reach is not None and (reach.get("total") or 0) > 0 and (reach.get("reached") or 0) == 0:
        return False
    oks = [t.get("ok") for t in tests.values() if isinstance(t, dict) and "ok" in t]
    if oks and not any(oks):
        return False
    return True


def components(tests: dict, th: dict) -> dict[str, float]:
    """Мгновенные под-скоры [0..1] из результатов последнего прогона."""
    comps: dict[str, float] = {}

    lat = tests.get("latency")
    if lat is not None:
        comps["latency"] = (
            lower_better(lat.get("ttfb_ms"), th["ttfb_good"], th["ttfb_bad"])
            if lat.get("ok") else 0.0
        )

    rel_req = rel_hold = reach_frac = None
    jit = tests.get("jitter")
    if jit is not None:
        if jit.get("ok"):
            comps["jitter"] = lower_better(jit.get("jitter_ms"), th["jitter_good"], th["jitter_bad"])
            rel_req = clamp01(1.0 - jit.get("loss_pct", 0) / 100.0)
            avg, jms = jit.get("avg_ms"), jit.get("jitter_ms")
            if avg and avg > 0 and jms is not None:
                comps["consistency"] = lower_better(jms / avg, th["cv_good"], th["cv_bad"])
        else:
            comps["jitter"] = 0.0
            comps["consistency"] = 0.0
            rel_req = 0.0

    # Единый транспорт-тест download даёт сразу три показателя: speed_mbps
    # (throughput), throttle_ratio и hold_ratio. limited=True — сервер отбил
    # запрос (429/5xx), НЕ вина ноды: не штрафуем.
    dl = tests.get("download")
    if dl is not None:
        # neutral: сервер ОТВЕТИЛ статусом (429/5xx = limited, либо 4xx = http_error) —
        # это не вина транспорта ноды, пропускную/hold НЕ штрафуем.
        neutral = bool(dl.get("limited") or dl.get("http_error"))
        if dl.get("ok"):
            tp = higher_better(dl.get("speed_mbps"), th["dl_min"], th["dl_target"])
            if tp is not None:                 # нет скорости (напр. limited) — не оцениваем
                comps["throughput"] = tp
        elif not neutral:
            comps["throughput"] = 0.0          # реальный провал транспорта
        ratio = dl.get("throttle_ratio")
        if ratio is not None:
            comps["throttle"] = higher_better(ratio, th["throttle_bad"], th["throttle_good"])
        elif not dl.get("ok") and not neutral:
            comps["throttle"] = 0.0
        if neutral:
            pass  # сервер ответил — hold не оцениваем
        elif dl.get("hold_ratio") is not None:
            rel_hold = clamp01(dl["hold_ratio"])
        elif not dl.get("ok"):
            rel_hold = 0.0

    reach = tests.get("reachability")
    if reach is not None:
        total = reach.get("total") or 0
        reach_frac = (reach.get("reached", 0) / total) if total else 0.0

    # reliability = комбинация доступного: успех запросов + выживаемость + reachability
    rel_parts = [(0.5, rel_req), (0.3, rel_hold), (0.2, reach_frac)]
    rel_parts = [(w, v) for w, v in rel_parts if v is not None]
    if rel_parts:
        wsum = sum(w for w, _ in rel_parts)
        comps["reliability"] = sum(w * v for w, v in rel_parts) / wsum

    return comps


def instant_score(tests: dict, th: dict, weights: dict) -> tuple[float, bool, dict]:
    """Мгновенный скор S_run [0..100], флаг gate и под-скоры."""
    gate = gate_ok(tests, th)
    comps = components(tests, th)
    if not gate:
        return 0.0, False, comps
    num = den = 0.0
    for name, w in weights.items():
        v = comps.get(name)
        if v is not None:
            num += w * v
            den += w
    score = (num / den * 100.0) if den > 0 else 0.0
    return score, True, comps


def aggregate(prev: Optional[dict], s_run: float, gate: bool, cfg) -> dict:
    """Обновить EWMA-состояние ноды и вернуть S_final + компоненты состояния."""
    a = cfg.alpha
    g = 1.0 if gate else 0.0
    if prev is None:
        ewma, flap, avail, samples = s_run, 0.0, g, 1
    else:
        pe = prev["score_ewma"]
        ewma = a * s_run + (1 - a) * pe
        flap = a * abs(s_run - pe) + (1 - a) * prev["flap"]
        avail = a * g + (1 - a) * prev["avail"]
        samples = prev["samples"] + 1

    if avail >= cfg.avail_full:
        avail_factor = 1.0
    elif avail <= cfg.avail_floor:
        avail_factor = 0.5
    else:
        avail_factor = 0.5 + 0.5 * (avail - cfg.avail_floor) / (cfg.avail_full - cfg.avail_floor)

    if not gate:
        # Нода провалила gate в этом прогоне — рейтинг обнуляется сразу,
        # без сглаживания (заблокирована/недоступна прямо сейчас).
        final = 0.0
    else:
        final = clamp(ewma * avail_factor - cfg.flap_lambda * flap, 0.0, 100.0)
    return {
        "score": round(final, 1),
        "score_ewma": round(ewma, 3),
        "avail": round(avail, 3),
        "flap": round(flap, 3),
        "samples": samples,
    }
