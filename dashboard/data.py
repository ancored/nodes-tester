"""Сбор данных для дашборда из единой БД stats.db (read-only)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path

from naming import parse_group, parse_node

_connection = ContextVar("dashboard_connection", default=None)

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


def node_detail(cfg, crc: str, include_config=False) -> dict:
    """Read one node's retained history; secrets only for the authenticated route."""
    from naming import node_payload, content_crc32
    db = cfg.storage.db_file
    if not cfg.storage.enabled or not db or not os.path.exists(db):
        return {}
    # closing(): the sqlite3 context manager only commits, it does not close.
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        node = con.execute("SELECT tag, payload FROM nodes WHERE crc=?", (crc,)).fetchone()
        if node is None:
            return {}
        history = [dict(r) for r in con.execute(
            "SELECT rowid AS sample_id, ts, score FROM score_history WHERE crc=? ORDER BY ts DESC, rowid DESC", (crc,))]
        result = {"score_history": history}
        if not include_config:
            return result
        fragment = None
        source = "sqlite"
        try:
            with open(cfg.storage.nodes_file, encoding="utf-8") as fh:
                config = json.load(fh)
            items = (config.get("outbounds", []) + config.get("endpoints", [])) if isinstance(config, dict) else config
            fragment = next((ob for ob in items if isinstance(ob, dict)
                             and parse_node(ob.get("tag", "")).node_id == crc), None)
        except (OSError, ValueError, TypeError):
            pass
        if fragment is not None:
            source = "nodes_file"
        else:
            try:
                payload = json.loads(node["payload"] or "null")
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                fragment = {"tag": node["tag"], **payload}
        if fragment is not None:
            canonical = node_payload(fragment)
            result.update(fragment=fragment, payload=canonical, crc_fields=list(json.loads(canonical)),
                          calculated_crc=content_crc32(fragment), fragment_source=source,
                          nodes_file=cfg.storage.nodes_file)
        return result


_FAILSAFE_SUFFIX = "-auto-out-failsafe"


def node_groups(nodes_file) -> tuple[list[str], dict[str, list[str]]]:
    """Группы из nodes.json: порядок групп и CRC ноды → группы, куда она входит.

    Состав берётся из urltest {group}-auto-out-failsafe (прямые члены — ноды).
    Нет файла — пустой результат: группы неизвестны, а не пусты."""
    try:
        with open(nodes_file, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError, TypeError):
        return [], {}
    order, by_crc = [], {}
    for ob in config.get("outbounds", []) if isinstance(config, dict) else []:
        tag = str(ob.get("tag", "")) if isinstance(ob, dict) else ""
        if not tag.endswith(_FAILSAFE_SUFFIX) or tag.startswith("global-"):
            continue
        group = tag[:-len(_FAILSAFE_SUFFIX)]
        order.append(group)
        for member in ob.get("outbounds") or []:
            crc = parse_node(str(member)).node_id
            if crc and group not in by_crc.setdefault(crc, []):
                by_crc[crc].append(group)
    return order, by_crc


def _query(db_path: str, sql: str) -> list[dict]:
    con = _connection.get()
    if con is not None:
        return [dict(r) for r in con.execute(sql).fetchall()]
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
    """One consistent read-only snapshot. Missing storage is not an empty database."""
    db = cfg.storage.db_file
    enabled = cfg.storage.enabled
    if not enabled or not db or not os.path.exists(db):
        from types import SimpleNamespace
        data = _collect(SimpleNamespace(storage=SimpleNamespace(db_file="", retention_days=cfg.storage.retention_days)))
        data["source"] = {"state": "missing" if enabled else "disabled",
                          "message": "База ещё не создана" if enabled else "Хранилище отключено"}
        return data
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    token = _connection.set(con)
    try:
        con.execute("BEGIN")
        data = _collect(cfg)
        data["source"] = {"state": "ok", "message": "Снимок SQLite",
                          "last_measurement": _query(db, "SELECT MAX(ts) AS ts FROM results WHERE test <> '_select'")[0]["ts"]}
        return data
    finally:
        _connection.reset(token)
        con.close()


def _collect(cfg) -> dict:
    db = cfg.storage.db_file

    # --- Рейтинг (таблица scores), активные сверху, далее по убыванию score ---
    rating = _scores(db)

    def _key(r):
        try:
            return (int(float(r.get("active") or 0)), float(r.get("score") or 0))
        except (TypeError, ValueError):
            return (0, 0.0)

    rating.sort(key=_key, reverse=True)

    # --- Группы нод (nodes.json): фильтры таблиц и качество по группам ---
    group_order, groups_of = node_groups(getattr(cfg.storage, "nodes_file", "") or "")
    for r in rating:
        r["groups"] = groups_of.get(r.get("id"), [])

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
    user_up = sum(r["up"] for r in rows)
    user_down = sum(r["down"] for r in rows)
    traffic_total = user_up + user_down
    tester_rows = _query(db, "SELECT COALESCE(SUM(up),0) AS up, COALESCE(SUM(down),0) AS down FROM traffic WHERE is_tester=1")
    tester_up = tester_rows[0]["up"] if tester_rows else 0
    tester_down = tester_rows[0]["down"] if tester_rows else 0
    cutoff = int(time.time()) - 86400
    tester_download_24h = _test_downloads(db, cutoff)
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
    for table in (nodes, results.get("rows", []), garbage, longevity, dropouts, rows):
        for r in table:
            r["groups"] = groups_of.get(r.get("crc"), [])
    traffic_range = _query(db, "SELECT MIN(ts) AS start, MAX(ts) AS end FROM traffic WHERE is_tester=0")
    tester_range = _query(db, "SELECT MIN(ts) AS start, MAX(ts) AS end FROM traffic WHERE is_tester=1")

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "groups": group_order,
        "rating": rating,
        "history": history,
        "provider_quality": provider_quality,
        "results": results,
        "traffic_providers": traffic_providers,
        "traffic_total": traffic_total,
        "traffic_user_totals": {"up": user_up, "down": user_down, "total": traffic_total},
        "traffic_tester_totals": {"up": tester_up, "down": tester_down, "total": tester_up + tester_down},
        "traffic_test_download_24h": tester_download_24h,
        "traffic_countries": traffic_countries,
        "traffic_protocols": traffic_protocols,
        "traffic_nodes": traffic_nodes,
        "node_traffic": [r for r in rows if r["kind"] == "leaf"],
        "traffic_rows": rows,                     # все строки: агрегаты и фильтры в админке
        "endpoints": endpoints,
        "garbage": garbage,
        "longevity": longevity,
        "dropouts": dropouts,
        "graveyard": graveyard,
        "attrition": attrition,
        "score_spark": score_spark,
        "nodes": nodes,
        "traffic_range": traffic_range[0] if traffic_range else {},
        "traffic_tester_range": tester_range[0] if tester_range else {},
        "retention_days": cfg.storage.retention_days,
        "node_events": _query(db, "SELECT ts, crc, event, reason, streak FROM node_events ORDER BY ts DESC, rowid DESC LIMIT 500"),
    }


def _test_downloads(db: str, cutoff: int) -> dict:
    """Known response-body bytes; short connections need not survive a poll."""
    rows = _query(db, f"""SELECT ts, metrics FROM results
        WHERE ts >= {int(cutoff)} AND test IN ('download', 'heavy_download')""")
    total, count, stamps = 0, 0, []
    for row in rows:
        try:
            value = json.loads(row.get("metrics") or "{}").get("downloaded")
        except (ValueError, AttributeError):
            continue
        if type(value) is int and value >= 0:
            total += value
            count += 1
            stamps.append(row["ts"])
    return {"down": total, "tests": count,
            "start": min(stamps) if stamps else None,
            "end": max(stamps) if stamps else None}


def _classify_traffic(r: dict) -> dict:
    """Классифицировать строку трафика: leaf / direct / unspecified / other."""
    up, down = r.get("up") or 0, r.get("down") or 0
    crc = r.get("crc")
    row = {"crc": crc, "up": up, "down": down, "total": up + down}
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
    """История переключений (activations): последние 200; active = последняя запись региона.
    Провайдер/протокол/cc/crc разбираем из тега ноды (как в остальных таблицах)."""
    rows = _query(db, "SELECT ts, region, tag, crc, reason, score, prev FROM activations "
                      "ORDER BY ts DESC, rowid DESC LIMIT 200")
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
    """Качество провайдеров: всего/мёртвых нод и средний рейтинг (без учёта мёртвых)
    по (провайдер, группа). Нода входит в каждую свою группу."""
    acc: dict = {}
    for r in rating:
        prov = r.get("provider") or "?"
        try:
            sc = float(r.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        for group in r.get("groups") or ["вне групп"]:
            a = acc.setdefault((prov, group), {"total": 0, "dead": 0, "sum": 0.0, "live": 0})
            a["total"] += 1
            if sc <= 0:
                a["dead"] += 1
            else:
                a["sum"] += sc
                a["live"] += 1
    out = [{"provider": prov, "group": group, "total": a["total"], "dead": a["dead"],
            "dead_pct": round(100 * a["dead"] / a["total"]) if a["total"] else 0,
            "avg": round(a["sum"] / a["live"], 1) if a["live"] else 0.0}
           for (prov, group), a in acc.items()]
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
               "heavy_download", "gemini"]
# Тесты отдельных фаз (для кандидатов групп): последний известный результат, вне прогона.
_PHASE_TESTS = ("heavy_download", "gemini")


def _cell(test: str, ok, metrics_json: str, error: str = "") -> dict:
    """Компактное значение теста для ячейки: {v, ok, title?}."""
    try:
        m = json.loads(metrics_json) if metrics_json else {}
    except (TypeError, ValueError):
        m = {}
    if not ok:
        if test == "gemini" and m.get("countries"):
            seen = sorted({c for c in m["countries"] if c})
            return {"v": "Провал" + (f" [{', '.join(seen)}]" if seen else ""), "ok": 0,
                    "title": (error or "").strip()}
        return {"v": "Провал", "ok": 0, "title": (error or "").strip()}
    if test == "connectivity":
        cc = m.get("country")
        return {"v": "Успешно" + (f" [{cc}]" if cc else ""), "ok": 1}
    if test == "latency":
        return {"v": f"{m.get('ttfb_ms', '?')} мс", "ok": 1}
    if test == "jitter":
        return {"v": f"{m.get('jitter_ms', '?')} мс / {m.get('loss_pct', 0)}%", "ok": 1}
    if test == "download":
        # единый транспорт-тест: скорость + троттлинг (+ метка limited при 429)
        thr = m.get("throttle_ratio")
        tag = " (ограничение сервера)" if m.get("limited") else ""
        spd = f"{m.get('speed_mbps', '?')} Мбит/с"
        return {"v": (f"{spd} ×{thr}{tag}" if thr is not None else f"{spd}{tag}"), "ok": 1}
    if test == "reachability":
        return {"v": f"{m.get('reached', '?')}/{m.get('total', '?')}", "ok": 1}
    if test == "heavy_download":         # veto-тест кандидатов: скорость (FAIL=veto)
        return {"v": f"{m.get('speed_mbps', '?')} Мбит/с", "ok": 1}
    if test == "gemini":                 # обязательный тест групп: страна по мнению Google
        return {"v": f"Google: {m.get('country') or '?'}", "ok": 1}
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


# Тесты, не входящие в «строго последний прогон» лёгких тестов:
#   _select      — служебная запись переключения (в таблице не показываем);
#   heavy_download — идёт вне прогона (every N), показываем как есть, но помечаем.
_OFF_PASS_TESTS = {"_select", *_PHASE_TESTS}


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
        light = [r for r in rws if r["test"] not in _PHASE_TESTS]
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
            node["cells"][r["test"]] = {**_cell(r["test"], r["ok"], r["metrics"], r.get("error")), "ts": r["ts"]}
            tests_seen.add(r["test"])
        # DL50 и gemini — последнее известное значение, вне прогона; помечаем off_pass.
        for test in _PHASE_TESTS:
            phase = [r for r in rws if r["test"] == test]
            if not phase:
                continue
            hr = max(phase, key=lambda r: r["ts"] or 0)
            cell = _cell(test, hr["ok"], hr["metrics"], hr.get("error"))
            cell["ts"] = hr["ts"]
            # Фаза пишется на пару минут позже лёгких тестов, но с тем же pass_no за тот
            # же день — это ТОТ ЖЕ прогон. «Вне прогона» = другой pass_no или другой день
            # (pass_no цикличен по дням, поэтому одного номера мало).
            off_pass = not _same_pass(hr, node["pass_no"], pass_ts)
            cell["heavy"] = 1
            cell["off_pass"] = 1 if off_pass else 0
            node["cells"][test] = cell
            tests_seen.add(test)
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
               g.reason AS gstate, g.until AS guntil, g.streak AS gstreak,
               g.until_pass AS until_pass
        FROM nodes n
        LEFT JOIN scores s ON s.crc = n.crc
        LEFT JOIN garbage g ON g.crc = n.crc
        ORDER BY n.present DESC, s.score DESC""")
    out = []
    meta = _query(db, "SELECT value FROM meta WHERE key='pass_seq'")
    try:
        pass_seq = int(meta[0]["value"]) if meta else 0
    except (ValueError, TypeError):
        pass_seq = 0
    for r in rows:
        out.append({
            "crc": r["crc"], "node": r["node"], "provider": r["provider"],
            "protocol": r["protocol"], "country": r["country"], "label": r["label"],
            "server": r["server"], "present": r["present"], "banned": r["banned"],
            "score": r["score"], "region": r["region"], "active": r["active"],
            "gstate": r["gstate"], "guntil": r["guntil"], "gstreak": r["gstreak"],
            "passes_left": max(0, int(r.get("until_pass") or 0) - pass_seq),
        })
    return out
