"""Сбор данных для дашборда из score.csv / switch_state.json / stats.db (read-only)."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import time

from naming import parse_group, parse_node


def _read_csv(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, ValueError):
        return []


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _query(db_path: str, sql: str) -> list[dict]:
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)   # только чтение
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def collect(cfg) -> dict:
    # --- Рейтинг (score.csv), активные сверху, далее по убыванию score ---
    rating = _read_csv(cfg.scoring.file)

    def _key(r):
        try:
            return (int(float(r.get("active") or 0)), float(r.get("score") or 0))
        except (TypeError, ValueError):
            return (0, 0.0)

    rating.sort(key=_key, reverse=True)

    db = cfg.storage.db_file

    # --- История переключений (таблица activations) ---
    history = _switch_history(db)
    # --- Качество провайдеров (из score.csv) ---
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

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rating": rating,
        "history": history,
        "provider_quality": provider_quality,
        "results": results,
        "traffic_providers": traffic_providers,
        "traffic_countries": traffic_countries,
        "traffic_protocols": traffic_protocols,
        "traffic_nodes": traffic_nodes,
        "endpoints": endpoints,
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


def _results(db: str) -> dict:
    """Свод: по ноде — свежий результат каждого теста (последний по ts)."""
    rows = _query(db, """
        SELECT r.crc AS crc, r.test AS test, r.ok AS ok, r.metrics AS metrics,
               r.error AS error, r.ts AS ts, r.pass_no AS pass_no,
               n.provider AS provider, n.protocol AS protocol, n.country AS country
        FROM results r
        JOIN (SELECT crc, test, MAX(ts) mts FROM results GROUP BY crc, test) m
          ON r.crc = m.crc AND r.test = m.test AND r.ts = m.mts
        LEFT JOIN nodes n ON n.crc = r.crc""")
    by_node: dict[str, dict] = {}
    tests_seen: set[str] = set()
    for r in rows:
        crc = r["crc"]
        tests_seen.add(r["test"])
        node = by_node.setdefault(crc, {
            "provider": r.get("provider"), "protocol": r.get("protocol"),
            "country": r.get("country"), "crc": crc, "ts": 0, "pass_no": 0, "cells": {}})
        node["cells"][r["test"]] = _cell(r["test"], r["ok"], r["metrics"], r.get("error"))
        node["ts"] = max(node["ts"], r["ts"] or 0)
        node["pass_no"] = max(node["pass_no"], r.get("pass_no") or 0)
    cols = [t for t in _TEST_ORDER if t in tests_seen]
    cols += sorted(tests_seen - set(cols))
    node_rows = sorted(by_node.values(), key=lambda x: x["ts"], reverse=True)
    for n in node_rows:                       # ярлык прогона "DD/MM-NNN" (дата из ts)
        lt = time.localtime(n["ts"]) if n.get("ts") else None
        n["pass_label"] = (f"{lt.tm_mday:02d}/{lt.tm_mon:02d}-{n['pass_no']:03d}"
                           if lt else str(n.get("pass_no", "")))
    return {"tests": cols, "rows": node_rows}
