"""Сбор данных для дашборда из единой БД stats.db (read-only)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from naming import parse_group, parse_node

# Колонки рейтинга (таблица scores) в порядке для дашборда; crc отдаём как 'id',
# last_pass — как 'last_seen' (совместимо с прежним score.csv).
_SCORE_SELECT = (
    "SELECT node, provider, protocol, region, country, crc AS id, active, score, "
    "reliability, consistency, throttle, jitter, latency, throughput, "
    "score_ewma, avail, flap, samples, last_pass AS last_seen, heavy_ok, heavy_ts "
    "FROM scores"
)


def _scores(db_path: str) -> list[dict]:
    """Снимок рейтинга из таблицы scores (замена чтения score.csv)."""
    return _query(db_path, _SCORE_SELECT)


def _query(db_path: str, sql: str) -> list[dict]:
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        # as_uri() корректно экранирует допустимые в Windows именах символы вроде '#'.
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)   # только чтение
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def collect(cfg) -> dict:
    db = cfg.storage.db_file

    # --- Рейтинг (таблица scores), активные сверху, далее по убыванию score ---
    rating = _scores(db)

    def _key(r):
        try:
            return (int(float(r.get("active") or 0)), float(r.get("score") or 0))
        except (TypeError, ValueError):
            return (0, 0.0)

    rating.sort(key=_key, reverse=True)

    # --- История переключений (таблица activations) ---
    history = _switch_history(db)
    # --- Качество провайдеров (из рейтинга) ---
    provider_quality = _provider_quality(rating)

    # --- Трафик (stats.db): сырьё по CRC + классификация в Python ---
    raw = _query(db, """
        SELECT t.crc AS crc, n.provider AS provider, n.protocol AS protocol,
               n.country AS country, n.tag AS tag,
               SUM(t.up) AS up, SUM(t.down) AS down, SUM(t.conns) AS conns
        FROM traffic t LEFT JOIN nodes n ON n.crc = t.crc
        WHERE t.is_tester = 0
        GROUP BY t.crc""")
    rows = [_classify_traffic(r) for r in raw]
    traffic_total = sum((r.get("up") or 0) + (r.get("down") or 0) for r in rows)
    traffic_providers = _sum_by(rows, "provider", {"leaf", "unspecified"})
    traffic_countries = _sum_by(rows, "cc", {"leaf", "unspecified"})
    traffic_protocols = _sum_by(rows, "protocol", {"leaf"})   # протокол известен у leaf-нод
    traffic_nodes = _top_nodes(rows, limit=10)

    endpoints = _query(db, """
        SELECT COALESCE(n.provider, e.crc) AS provider, e.source_ip AS source_ip,
               e.dest_host AS dest_host, e.network AS network,
               e.up AS up, e.down AS down, e.flows AS flows
        FROM endpoints e LEFT JOIN nodes n ON n.crc = e.crc
        WHERE e.source_ip <> '127.0.0.1'          -- скрыть тестовый трафик
        ORDER BY e.down DESC LIMIT 50""")

    results = _results(db)

    # --- Жизненный цикл / деградация нод (node_events + garbage + nodes) ---
    ev = _event_stats(db)                 # crc -> {garbage_count, last_garbage, fails, first_garbage}
    garbage = _garbage_table(db, ev)
    longevity, dropouts = _degradation(db, ev)
    graveyard = _graveyard(db, ev, int(getattr(cfg.storage, "retention_days", 30) or 30))
    attrition = _attrition(db)
    score_spark = _score_spark(db)
    nodes = _nodes_list(db)

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rating": rating,
        "history": history,
        "provider_quality": provider_quality,
        "results": results,
        "traffic_providers": traffic_providers,
        "traffic_total": traffic_total,
        "traffic_countries": traffic_countries,
        "traffic_protocols": traffic_protocols,
        "traffic_nodes": traffic_nodes,
        "endpoints": endpoints,
        "garbage": garbage,
        "longevity": longevity,
        "dropouts": dropouts,
        "graveyard": graveyard,
        "attrition": attrition,
        "score_spark": score_spark,
        "nodes": nodes,
    }


def _classify_traffic(r: dict) -> dict:
    """Классифицировать строку трафика: leaf / direct / unspecified / other."""
    up, down = r.get("up") or 0, r.get("down") or 0
    crc = r.get("crc")
    row = {"up": up, "down": down, "total": up + down}
    if r.get("provider") is not None:                 # leaf-нода (есть запись в nodes)
        row.update(kind="leaf", provider=r["provider"], protocol=r.get("protocol"),
                   cc=r.get("country"), node=r.get("tag") or crc)
    elif crc in ("direct", "direct-out"):
        row.update(kind="direct", provider="direct", protocol=None, cc=None, node="direct-out")
    else:
        pg = parse_group(crc or "")                   # трафик через *-failsafe urltest
        if pg:
            row.update(kind="unspecified", provider=pg[0], protocol=None,
                       cc="undef", node="unspecified")
        else:
            row.update(kind="other", provider=crc, protocol=None, cc=None, node=crc)
    return row


def _sum_by(rows: list[dict], field: str, include: set) -> list[dict]:
    """Свернуть трафик по полю (provider/cc), суммируя up/down/total."""
    acc: dict = {}
    for r in rows:
        if r["kind"] not in include:
            continue
        k = r.get(field)
        if k is None:
            continue
        a = acc.setdefault(k, {"up": 0, "down": 0, "total": 0})
        a["up"] += r["up"]; a["down"] += r["down"]; a["total"] += r["total"]
    out = [{field: k, **v} for k, v in acc.items()]
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def _switch_history(db: str) -> list[dict]:
    """История переключений (activations): последние 20; активна = последняя по региону.
    Провайдер/протокол/cc/crc разбираем из тега ноды (как в остальных таблицах)."""
    rows = _query(db, "SELECT ts, region, tag, crc, reason, score FROM activations "
                      "ORDER BY ts DESC LIMIT 20")
    latest = {r["region"]: r["mts"] for r in _query(
        db, "SELECT region, MAX(ts) AS mts FROM activations GROUP BY region")}
    for r in rows:
        ident = parse_node(r.get("tag") or "")
        r["provider"] = ident.provider
        r["protocol"] = ident.protocol
        r["cc"] = ident.country
        r["crc"] = r.get("crc") or ident.node_id
        r["active"] = 1 if r.get("ts") == latest.get(r.get("region")) else 0
        reason = (r.get("reason") or "")
        r["emergency"] = 1 if reason.startswith("emergency") else 0   # emergency / -stuck
        r["stuck"] = 1 if reason == "emergency-stuck" else 0          # без замены
    return rows


def _provider_quality(rating: list[dict]) -> list[dict]:
    """Качество провайдеров из score.csv: всего/мёртвых нод и средний рейтинг
    (без учёта мёртвых), по (провайдер, регион)."""
    acc: dict = {}
    for r in rating:
        prov = r.get("provider") or "?"
        region = r.get("region") or "?"
        try:
            sc = float(r.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        a = acc.setdefault((prov, region), {"total": 0, "dead": 0, "sum": 0.0, "live": 0})
        a["total"] += 1
        if sc <= 0:
            a["dead"] += 1
        else:
            a["sum"] += sc
            a["live"] += 1
    out = [{"provider": prov, "region": region, "total": a["total"], "dead": a["dead"],
            "dead_pct": round(100 * a["dead"] / a["total"]) if a["total"] else 0,
            "avg": round(a["sum"] / a["live"], 1) if a["live"] else 0.0}
           for (prov, region), a in acc.items()]
    out.sort(key=lambda x: x["avg"], reverse=True)
    return out


def _top_nodes(rows: list[dict], limit: int = 10) -> list[dict]:
    """Топ-N leaf-нод (+ direct-out) по суммарному трафику."""
    acc: dict = {}
    for r in rows:
        if r["kind"] not in ("leaf", "direct"):
            continue
        a = acc.setdefault(r["node"], {"up": 0, "down": 0, "total": 0})
        a["up"] += r["up"]; a["down"] += r["down"]; a["total"] += r["total"]
    out = [{"node": k, **v} for k, v in acc.items()]
    out.sort(key=lambda x: x["total"], reverse=True)
    return out[:limit]


# Порядок колонок-тестов в своде результатов.
_TEST_ORDER = ["connectivity", "latency", "jitter", "download", "reachability",
               "heavy_download"]


def _cell(test: str, ok, metrics_json: str, error: str = "") -> dict:
    """Компактное значение теста для ячейки: {v, ok, title?}."""
    try:
        m = json.loads(metrics_json) if metrics_json else {}
    except (TypeError, ValueError):
        m = {}
    if not ok:
        return {"v": "FAIL", "ok": 0, "title": (error or "").strip()}
    if test == "connectivity":
        cc = m.get("country")
        return {"v": "OK" + (f"[{cc}]" if cc else ""), "ok": 1}
    if test == "latency":
        return {"v": f"{m.get('ttfb_ms', '?')}ms", "ok": 1}
    if test == "jitter":
        return {"v": f"{m.get('jitter_ms', '?')}ms {m.get('loss_pct', 0)}%", "ok": 1}
    if test == "download":
        # единый транспорт-тест: скорость + троттлинг (+ метка limited при 429)
        thr = m.get("throttle_ratio")
        tag = " lim" if m.get("limited") else ""
        spd = f"{m.get('speed_mbps', '?')}M"
        return {"v": (f"{spd} ×{thr}{tag}" if thr is not None else f"{spd}{tag}"), "ok": 1}
    if test == "reachability":
        return {"v": f"{m.get('reached', '?')}/{m.get('total', '?')}", "ok": 1}
    if test == "heavy_download":         # veto-тест кандидатов: скорость (FAIL=veto)
        return {"v": f"{m.get('speed_mbps', '?')}M", "ok": 1}
    return {"v": "ok", "ok": 1}


def _same_pass(hr: dict, pass_no, pass_ts: int) -> bool:
    """DL50-строка относится к тому же прогону, что и лёгкие тесты: совпадают pass_no и
    календарный день (pass_no цикличен по дням, поэтому сверяем ещё и дату)."""
    if (hr.get("pass_no") or 0) != (pass_no or 0):
        return False
    ht, pt = hr.get("ts") or 0, pass_ts or 0
    if not ht or not pt:
        return False
    a, b = time.localtime(ht), time.localtime(pt)
    return (a.tm_year, a.tm_yday) == (b.tm_year, b.tm_yday)


def _heavy_note(hr: dict, off_pass: bool) -> str:
    """Примечание для ячейки DL50: это отдельный veto-тест кандидатов, идущий вне
    обычного прогона (раз в N прогонов), поэтому значение может быть из другого пасса."""
    note = "DL50 — отдельный veto-тест кандидатов, идёт вне обычного прогона."
    if off_pass and hr.get("ts"):
        lt = time.localtime(hr["ts"])
        note += (f" Это значение измерено {lt.tm_mday:02d}/{lt.tm_mon:02d} "
                 f"{lt.tm_hour:02d}:{lt.tm_min:02d} (прогон {hr.get('pass_no')}), "
                 f"не в последнем прогоне ноды.")
    return note


# Тесты, не входящие в «строго последний прогон» лёгких тестов:
#   _select      — служебная запись переключения (в таблице не показываем);
#   heavy_download — идёт вне прогона (every N), показываем как есть, но помечаем.
_OFF_PASS_TESTS = {"_select", "heavy_download"}


def _results(db: str) -> dict:
    """Свод по ноде — строго ОДИН последний прогон (все лёгкие тесты берём из пасса
    с максимальным ts, а не независимо по каждому тесту, иначе в таблице мешаются
    данные разных прогонов). Исключение — DL50 (heavy_download): он идёт вне прогона,
    поэтому берём его последнее известное значение и помечаем отдельно. Служебная
    запись _select ('переключ.') в таблицу не выводится вовсе."""
    rows = _query(db, """
        SELECT r.crc AS crc, r.test AS test, r.ok AS ok, r.metrics AS metrics,
               r.error AS error, r.ts AS ts, r.pass_no AS pass_no,
               n.provider AS provider, n.protocol AS protocol, n.country AS country
        FROM results r LEFT JOIN nodes n ON n.crc = r.crc""")
    per_node: dict[str, list] = {}
    for r in rows:
        if r["test"] == "_select":            # служебная запись — в таблицу не идёт
            continue
        per_node.setdefault(r["crc"], []).append(r)

    tests_seen: set[str] = set()
    node_rows: list[dict] = []
    for crc, rws in per_node.items():
        light = [r for r in rws if r["test"] != "heavy_download"]
        # ts последнего прогона — по лёгким тестам (все они пишутся одним ts за пасс).
        base_pool = light or rws
        base_row = max(base_pool, key=lambda r: r["ts"] or 0)
        pass_ts = base_row["ts"] or 0
        node = {"provider": base_row.get("provider"), "protocol": base_row.get("protocol"),
                "country": base_row.get("country"), "crc": crc,
                "ts": pass_ts, "pass_no": base_row.get("pass_no") or 0, "cells": {}}
        # Лёгкие тесты — только из последнего прогона (ts == pass_ts).
        for r in light:
            if (r["ts"] or 0) != pass_ts:
                continue
            node["cells"][r["test"]] = _cell(r["test"], r["ok"], r["metrics"], r.get("error"))
            tests_seen.add(r["test"])
        # DL50 — последнее известное значение, вне прогона; помечаем off_pass.
        heavy = [r for r in rws if r["test"] == "heavy_download"]
        if heavy:
            hr = max(heavy, key=lambda r: r["ts"] or 0)
            cell = _cell("heavy_download", hr["ok"], hr["metrics"], hr.get("error"))
            # DL50 пишется на пару минут позже лёгких тестов, но с тем же pass_no за тот
            # же день — это ТОТ ЖЕ прогон. «Вне прогона» = другой pass_no или другой день
            # (pass_no цикличен по дням, поэтому одного номера мало).
            off_pass = not _same_pass(hr, node["pass_no"], pass_ts)
            cell["heavy"] = 1
            cell["off_pass"] = 1 if off_pass else 0
            cell["title"] = _heavy_note(hr, off_pass)
            node["cells"]["heavy_download"] = cell
            tests_seen.add("heavy_download")
        node_rows.append(node)

    cols = [t for t in _TEST_ORDER if t in tests_seen]
    cols += sorted(tests_seen - set(cols))
    node_rows.sort(key=lambda x: x["ts"], reverse=True)
    for n in node_rows:                       # ярлык прогона "DD/MM-NNN" (дата из ts)
        lt = time.localtime(n["ts"]) if n.get("ts") else None
        n["pass_label"] = (f"{lt.tm_mday:02d}/{lt.tm_mon:02d}-{n['pass_no']:03d}"
                           if lt else str(n.get("pass_no", "")))
    return {"tests": cols, "rows": node_rows}


# --- Жизненный цикл / деградация нод ------------------------------------

def _event_stats(db: str) -> dict:
    """crc -> агрегаты node_events: gcount (раз в garbage), last/first_garbage, fails."""
    rows = _query(db, """
        SELECT crc,
               SUM(event = 'garbage')  AS gcount,
               MAX(CASE WHEN event = 'garbage' THEN ts END) AS last_garbage,
               MIN(CASE WHEN event = 'garbage' THEN ts END) AS first_garbage,
               SUM(event IN ('backoff', 'garbage')) AS fails
        FROM node_events GROUP BY crc""")
    return {r["crc"]: r for r in rows}


def _garbage_table(db: str, ev: dict) -> list[dict]:
    """Ноды, которые СЕЙЧАС в подписке и в паузе (backoff) / карантине (garbage).
    Удалённые из подписки сюда не попадают — они на «Кладбище» (_graveyard)."""
    now = int(time.time())
    meta = _query(db, "SELECT value FROM meta WHERE key = 'pass_seq'")
    try:
        seq = int(meta[0]["value"]) if meta else 0
    except (TypeError, ValueError):
        seq = 0
    rows = _query(db, """
        SELECT g.crc AS crc, g.since AS since, g.until AS until, g.reason AS state,
               g.streak AS streak, g.until_pass AS until_pass,
               n.provider AS provider, n.protocol AS protocol, n.country AS cc,
               n.first_seen AS first_seen, n.present AS present
        FROM garbage g LEFT JOIN nodes n ON n.crc = g.crc""")
    out = []
    for r in rows:
        if r.get("present") != 1:                    # удалённые — на «Кладбище»
            continue
        e = ev.get(r["crc"], {})
        out.append({
            "provider": r.get("provider"), "protocol": r.get("protocol"),
            "cc": r.get("cc"), "crc": r["crc"], "first_seen": r.get("first_seen"),
            "state": r.get("state"), "streak": r.get("streak"),
            "last_garbage": e.get("last_garbage"), "garbage_count": e.get("gcount") or 0,
            "in_garbage": max(0, now - int(r["since"] or now)), "until": r.get("until"),
            # пауза: сколько прогонов ещё пропустит (0 = пробуется в ближайшем)
            "passes_left": (max(0, int(r["until_pass"] or 0) - seq)
                            if r.get("state") == "backoff" else None),
        })
    out.sort(key=lambda x: (x["state"] != "garbage", -(x["in_garbage"] or 0)))
    return out


def _degradation(db: str, ev: dict) -> tuple[list, list]:
    """Долгожители и быстро выпадающие — только ноды, которые СЕЙЧАС в подписке
    (удалённые — на «Кладбище»).

    Долгожители: в строю (score > 0), не в паузе/карантине, не забанены; по возрасту ↓.
    Быстро выпадающие: хоть раз были в карантине; по сроку жизни до 1-го карантина ↑."""
    now = int(time.time())
    nodes = _query(db, """
        SELECT n.crc AS crc, n.provider AS provider, n.protocol AS protocol,
               n.country AS cc, n.first_seen AS first_seen,
               COALESCE(n.banned, 0) AS banned,
               s.score AS score, s.active AS active, g.reason AS gstate
        FROM nodes n
        LEFT JOIN scores s ON s.crc = n.crc
        LEFT JOIN garbage g ON g.crc = n.crc
        WHERE n.present = 1""")
    longevity, dropouts = [], []
    for r in nodes:
        e = ev.get(r["crc"], {})
        fs = int(r["first_seen"] or now)
        base = {"provider": r.get("provider"), "protocol": r.get("protocol"),
                "cc": r.get("cc"), "crc": r["crc"], "first_seen": fs,
                "garbage_count": e.get("gcount") or 0, "fails": e.get("fails") or 0}
        try:
            score = float(r.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score > 0 and not r.get("gstate") and not r.get("banned"):
            longevity.append({**base, "age": max(0, now - fs),
                              "score": r.get("score"), "active": r.get("active") or 0})
        if e.get("first_garbage"):                    # была в карантине → выпадающая
            dropouts.append({**base, "first_garbage": e["first_garbage"],
                             "lifespan": max(0, int(e["first_garbage"]) - fs)})
    longevity.sort(key=lambda x: x["age"], reverse=True)     # старейшие сверху
    dropouts.sort(key=lambda x: x["lifespan"])               # короткоживущие сверху
    return longevity[:40], dropouts[:40]


def _downsample(vals: list, n: int) -> list:
    """Проредить ряд до n точек (равномерно, последняя точка сохраняется)."""
    if len(vals) <= n:
        return vals
    step = (len(vals) - 1) / (n - 1)
    return [vals[round(i * step)] for i in range(n)]


def _graveyard(db: str, ev: dict, retention_days: int) -> list[dict]:
    """«Кладбище»: ноды, ушедшие из подписки (present = 0). Хранятся в БД ещё
    retention_days после last_seen, затем cleanup() стирает их целиком.

    Для каждой: сколько прожила (first_seen → удаление), траектория рейтинга из
    score_history (пик/среднее/последний + прореженный ряд для спарклайна),
    сколько раз была в карантине и в каком состоянии ушла."""
    now = int(time.time())
    nodes = _query(db, """
        SELECT n.crc AS crc, n.tag AS tag, n.provider AS provider, n.protocol AS protocol,
               n.country AS cc, n.first_seen AS first_seen, n.last_seen AS last_seen,
               COALESCE(n.banned, 0) AS banned, g.reason AS gstate,
               (SELECT MAX(ts) FROM node_events e
                 WHERE e.crc = n.crc AND e.event = 'removed') AS removed_at
        FROM nodes n LEFT JOIN garbage g ON g.crc = n.crc
        WHERE n.present = 0""")
    if not nodes:
        return []
    hist: dict[str, list] = {}
    for r in _query(db, """
            SELECT h.crc AS crc, h.score AS score FROM score_history h
            JOIN nodes n ON n.crc = h.crc WHERE n.present = 0
            ORDER BY h.crc, h.ts"""):
        hist.setdefault(r["crc"], []).append(round(float(r["score"] or 0), 1))
    out = []
    for r in nodes:
        e = ev.get(r["crc"], {})
        fs = int(r["first_seen"] or now)
        ls = int(r["last_seen"] or fs)
        removed = int(r["removed_at"] or ls)
        h = hist.get(r["crc"], [])
        last = h[-1] if h else None
        if r.get("banned"):
            cause = "banned"
        elif r.get("gstate") == "garbage":
            cause = "quarantine"                      # ушла из карантина
        elif r.get("gstate") == "backoff" or (last is not None and last <= 0):
            cause = "zero"                            # ушла с нулевым рейтингом / на паузе
        elif last is None:
            cause = "untested"
        else:
            cause = "alive"                           # провайдер убрал рабочую ноду
        out.append({
            "provider": r.get("provider"), "protocol": r.get("protocol"), "cc": r.get("cc"),
            "crc": r["crc"], "tag": r.get("tag"),
            "first_seen": fs, "removed_at": removed,
            "lifespan": max(0, removed - fs),
            "purge_in": max(0, ls + retention_days * 86400 - now),
            "peak": max(h) if h else None,
            "avg": round(sum(h) / len(h), 1) if h else None,
            "last": last, "samples": len(h),
            "spark": _downsample(h, 30),
            "garbage_count": e.get("gcount") or 0, "fails": e.get("fails") or 0,
            "cause": cause,
        })
    out.sort(key=lambda x: x["removed_at"], reverse=True)   # свежие потери сверху
    return out


def _attrition(db: str) -> list[dict]:
    """Динамика по дням: added/removed/garbage/recovered (локальная дата)."""
    return _query(db, """
        SELECT date(ts, 'unixepoch', 'localtime') AS day,
               SUM(event = 'added')     AS added,
               SUM(event = 'removed')   AS removed,
               SUM(event = 'garbage')   AS garbage,
               SUM(event = 'recovered') AS recovered
        FROM node_events GROUP BY day ORDER BY day""")


def _score_spark(db: str, points: int = 24) -> dict:
    """crc -> последние N значений score (по ts ↑) для спарклайна истории рейтинга."""
    out: dict[str, list] = {}
    for r in _query(db, "SELECT crc, score FROM score_history ORDER BY crc, ts"):
        out.setdefault(r["crc"], []).append(round(float(r["score"] or 0), 1))
    return {c: v[-points:] for c, v in out.items()}


def _nodes_list(db: str) -> list[dict]:
    """Полный список нод (таблица nodes) для раздела «Управление» админки.

    Для каждой ноды — идентичность (crc/tag/provider/protocol/country/label/server),
    флаги present/banned, счёт рейтинга (score/region/active из scores) и текущий
    backoff/карантин (из garbage). Бан (nodes.banned) вносится админкой (api_control).
    """
    rows = _query(db, """
        SELECT n.crc AS crc, n.tag AS node, n.provider AS provider, n.protocol AS protocol,
               n.country AS country, n.label AS label, n.server AS server,
               n.present AS present, COALESCE(n.banned, 0) AS banned,
               s.score AS score, s.region AS region, s.active AS active,
               g.reason AS gstate, g.until AS guntil, g.streak AS gstreak
        FROM nodes n
        LEFT JOIN scores s ON s.crc = n.crc
        LEFT JOIN garbage g ON g.crc = n.crc
        ORDER BY n.present DESC, s.score DESC""")
    out = []
    for r in rows:
        out.append({
            "crc": r["crc"], "node": r["node"], "provider": r["provider"],
            "protocol": r["protocol"], "country": r["country"], "label": r["label"],
            "server": r["server"], "present": r["present"], "banned": r["banned"],
            "score": r["score"], "region": r["region"], "active": r["active"],
            "gstate": r["gstate"], "guntil": r["guntil"], "gstreak": r["gstreak"],
        })
    return out
