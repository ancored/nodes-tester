"""Сбор данных для дашборда из единой БД stats.db (read-only)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path

from naming import parse_node
from naming.regions import alpha3_to_alpha2
from nodes_tester.tests.gemini import DEFAULT_FORBIDDEN as GEMINI_FORBIDDEN

_connection = ContextVar("dashboard_connection", default=None)

# Колонки рейтинга (таблица scores) в порядке для дашборда; crc отдаём как 'id'.
_SCORE_SELECT = (
    "SELECT node, provider, protocol, region, country, crc AS id, active, score, "
    "reliability, consistency, throttle, jitter, latency, throughput, "
    "score_ewma, avail, flap, samples, heavy_ok, heavy_ts, reserve "
    "FROM scores"
)


def node_detail(cfg, crc: str, include_config=False) -> dict:
    """Read one node's retained history; secrets only for the authenticated route."""
    from naming import canonical, credential_paths, node_key, node_payload, split_payload
    db = cfg.storage.path
    if not db or not os.path.exists(db):
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
            with open(cfg.nodes_file, encoding="utf-8") as fh:
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
            # Две строки, которые хешируются: настройки сервера (node_crc) и учётные
            # данные (cred_crc); пути учётных данных — для подсветки JSON в карточке.
            settings, creds = split_payload(fragment)
            result.update(fragment=fragment, node_payload=canonical(settings),
                          cred_payload=canonical(creds), crc_fields=list(node_payload(fragment)),
                          cred_paths=credential_paths(fragment),
                          calculated_crc=node_key(fragment), fragment_source=source,
                          nodes_file=cfg.nodes_file)
        return result


PROVIDER_WINDOW = 7 * 86400


def provider_card(cfg, tag: str, source: "dict | None") -> dict:
    """Карточка провайдера (подписки): ноды и их живость, доля времени активной нодой в
    группах за PROVIDER_WINDOW, проход AI-проверок, трафик, причины последних провалов;
    source — состояние подписки по последнему опросу (срок и трафик тарифа)."""
    from nodes_tester.diagnose import cause
    out = {"tag": tag, "window": PROVIDER_WINDOW, "source": source,
           "nodes": {"total": 0, "live": 0, "restricted": 0, "active": [], "reserve": 0},
           "activity": [], "ai": {}, "traffic": {"up": 0, "down": 0}, "causes": {}}
    db = cfg.storage.path
    if not db or not os.path.exists(db):
        return out
    now = int(time.time())
    since = now - PROVIDER_WINDOW
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        nodes = [dict(r) for r in con.execute("""
            SELECT n.crc, s.score, s.active, s.reserve, g.until
            FROM nodes n LEFT JOIN scores s ON s.crc = n.crc LEFT JOIN garbage g ON g.crc = n.crc
            WHERE n.provider = ? AND n.present = 1""", (tag,))]
        out["nodes"].update(
            total=len(nodes), live=sum(1 for n in nodes if (n["score"] or 0) > 0),
            restricted=sum(1 for n in nodes if (n["until"] or 0) > now),
            reserve=sum(1 for n in nodes if n["reserve"]))
        # Активации до начала окна тоже нужны: группа могла стоять на ноде с тех пор.
        acts = con.execute("""
            SELECT region, tag, reason, ts FROM activations
            WHERE ts >= (SELECT COALESCE(MAX(ts), 0) FROM activations a2
                         WHERE a2.region = activations.region AND a2.ts < ?)
            ORDER BY region, ts""", (since,)).fetchall()
        out["activity"], out["nodes"]["active"] = _provider_activity(tag, acts, since, now)
        latest = con.execute("""
            SELECT r.crc, r.test, r.ok, r.metrics, r.error FROM results r
            JOIN (SELECT crc, test, MAX(ts) AS ts FROM results
                  WHERE crc IN (SELECT crc FROM nodes WHERE provider = ? AND present = 1)
                  GROUP BY crc, test) m ON m.crc = r.crc AND m.test = r.test AND m.ts = r.ts""",
                             (tag,)).fetchall()
        failed: dict = {}
        for r in latest:
            if r["test"] in ("gemini", "openai", "anthropic"):
                ai = out["ai"].setdefault(r["test"], {"ok": 0, "total": 0})
                ai["total"] += 1
                ai["ok"] += 1 if r["ok"] else 0
            if not r["ok"]:
                try:
                    metrics = json.loads(r["metrics"] or "{}")
                except ValueError:
                    metrics = {}
                code = cause(r["test"], metrics, r["error"] or "")
                # Одна причина на ноду: самая ранняя по ходу проверки (связность раньше скорости).
                if code and (r["crc"] not in failed or r["test"] == "connectivity"):
                    failed[r["crc"]] = code
        out["causes"] = dict(Counter(failed.values()))
        row = con.execute("""
            SELECT COALESCE(SUM(t.up), 0) AS up, COALESCE(SUM(t.down), 0) AS down
            FROM traffic t JOIN nodes n ON n.crc = t.crc
            WHERE n.provider = ? AND t.is_tester = 0 AND t.ts >= ?""", (tag, since)).fetchone()
        out["traffic"] = {"up": row["up"], "down": row["down"]}
    return out


def _provider_activity(tag: str, acts, since: int, now: int) -> tuple[list, list]:
    """По группам: доля окна, когда активной была нода провайдера, число его активаций и
    аварийных уходов с его нод; плюс группы, где его нода активна сейчас."""
    by_group: dict = {}
    for a in acts:
        by_group.setdefault(a["region"], []).append(a)
    activity, active_now = [], []
    for group, rows in sorted(by_group.items()):
        held = switches = emergencies = 0
        mine = [parse_node(a["tag"] or "").provider == tag for a in rows]
        for i, a in enumerate(rows):
            start = max(a["ts"], since)
            end = rows[i + 1]["ts"] if i + 1 < len(rows) else now
            if mine[i] and end > start:
                held += end - start
            if a["ts"] < since:
                continue
            if mine[i]:
                switches += 1
            if i > 0 and mine[i - 1] and str(a["reason"]).startswith("emergency"):
                emergencies += 1
        if mine[-1]:
            active_now.append(group)
        if held or switches:
            activity.append({"group": group, "share": round(held / (now - since), 3), "seconds": held,
                             "activations": switches, "emergencies": emergencies})
    return activity, active_now


_AUTO_SUFFIX = "-auto-out"


def node_groups(nodes_file, kind="selector") -> tuple[list[str], dict[str, list[str]]]:
    """Группы из nodes.json (whnodes.json клиентов — kind="urltest"): порядок групп и ключ ноды →
    группы, куда она входит.

    Состав берётся из {group}-auto-out (члены с ключом — ноды; failsafe, block, direct
    пропускаются). Нет файла — пустой результат: группы неизвестны, а не пусты."""
    try:
        with open(nodes_file, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError, TypeError):
        return [], {}
    order, by_crc = [], {}
    for ob in config.get("outbounds", []) if isinstance(config, dict) else []:
        tag = str(ob.get("tag", "")) if isinstance(ob, dict) else ""
        if not tag.endswith(_AUTO_SUFFIX) or tag.startswith("global-") or ob.get("type") != kind:
            continue
        group = tag[:-len(_AUTO_SUFFIX)]
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
    db = cfg.storage.path
    if not db or not os.path.exists(db):
        from types import SimpleNamespace
        data = _collect(SimpleNamespace(storage=SimpleNamespace(path="", retention_days=cfg.storage.retention_days),
                                        nodes_file=""))
        data["source"] = {"state": "missing", "message": "База ещё не создана"}
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
    db = cfg.storage.path

    # --- Рейтинг (таблица scores), активные сверху, далее по убыванию score ---
    rating = _query(db, _SCORE_SELECT)

    def _key(r):
        try:
            return (int(float(r.get("active") or 0)), float(r.get("score") or 0))
        except (TypeError, ValueError):
            return (0, 0.0)

    rating.sort(key=_key, reverse=True)

    # --- Группы нод (nodes.json): фильтры таблиц и качество по группам ---
    group_order, groups_of = node_groups(getattr(cfg, "nodes_file", "") or "")
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
    traffic_providers = _sum_by(rows, "provider", {"leaf"})
    traffic_countries = _sum_by(rows, "cc", {"leaf"})
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
    # Ушедшие из подписок ноды — только в «Архиве» (_graveyard): их строки помечены removed,
    # «Результаты» их не показывают, карточка ноды — показывает. Ключи, которых нет в базе
    # (прежние ноды), не отдаём.
    archived = {g["crc"] for g in graveyard}
    known = {n["crc"] for n in nodes} | archived
    results["rows"] = [r for r in results.get("rows", []) if r["crc"] in known]
    for r in results["rows"]:
        if r["crc"] in archived:
            r["removed"] = 1
    # Клиентские группы — по собранному whnodes.json: что получили клиенты.
    _, client_groups_of = node_groups(getattr(cfg, "whnodes_file", "") or "", kind="urltest")
    for table in (nodes, results["rows"], garbage, longevity, dropouts, rows):
        for r in table:
            r["groups"] = groups_of.get(r.get("crc"), [])
            r["client_groups"] = client_groups_of.get(r.get("crc"), [])
    chains = _chains(db)
    for table in (rating, nodes, results["rows"]):
        for r in table:
            r.update(chains.get(r.get("crc") or r.get("id"), {}))
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
    """Классифицировать строку трафика: leaf / direct / other."""
    up, down = r.get("up") or 0, r.get("down") or 0
    crc = r.get("crc")
    row = {"crc": crc, "up": up, "down": down, "total": up + down}
    if r.get("provider") is not None:                 # leaf-нода (есть запись в nodes)
        row.update(kind="leaf", provider=r["provider"], protocol=r.get("protocol"),
                   cc=r.get("country"), node=r.get("tag") or crc)
    elif crc in ("direct", "direct-out"):
        row.update(kind="direct", provider="direct", protocol=None, cc=None, node="direct-out")
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
    Провайдер/протокол/crc — из тега ноды, страна — измеренная (таблица nodes)."""
    rows = _query(db, "SELECT a.ts AS ts, a.region AS region, a.tag AS tag, a.crc AS crc, "
                      "a.reason AS reason, a.score AS score, a.prev AS prev, n.country AS cc "
                      "FROM activations a LEFT JOIN nodes n ON n.crc = a.crc "
                      "ORDER BY a.ts DESC, a.rowid DESC LIMIT 200")
    latest = {r["region"]: r["mts"] for r in _query(
        db, "SELECT region, MAX(ts) AS mts FROM activations GROUP BY region")}
    for r in rows:
        ident = parse_node(r.get("tag") or "")
        r["provider"] = ident.provider
        r["protocol"] = ident.protocol
        r["cc"] = r.get("cc") or ""
        r["crc"] = r.get("crc") or ident.node_id
        r["active"] = 1 if r.get("ts") == latest.get(r.get("region")) else 0
        reason = (r.get("reason") or "")
        r["emergency"] = 1 if reason.startswith("emergency") else 0   # emergency / -stuck
        r["stuck"] = 1 if reason == "emergency-stuck" else 0          # без замены
    return rows


def _provider_quality(rating: list[dict]) -> list[dict]:
    """Качество провайдеров: всего/мёртвых нод и средний рейтинг (без учёта мёртвых).
    По группам не делим: в каждой группе все ноды."""
    acc: dict = {}
    for r in rating:
        sc = _float(r.get("score"))
        a = acc.setdefault(r.get("provider") or "?", {"total": 0, "dead": 0, "sum": 0.0, "live": 0})
        a["total"] += 1
        if sc <= 0:
            a["dead"] += 1
        else:
            a["sum"] += sc
            a["live"] += 1
    out = [{"provider": prov, "total": a["total"], "dead": a["dead"],
            "dead_pct": round(100 * a["dead"] / a["total"]) if a["total"] else 0,
            "avg": round(a["sum"] / a["live"], 1) if a["live"] else 0.0}
           for prov, a in acc.items()]
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
               "heavy_download", "gemini", "google_country", "openai", "anthropic", "whitelist"]
_AI_TESTS = ("gemini", "openai", "anthropic", "upstream")
# Страны, где сервисы Google AI недоступны (alpha-2), — для отметки в колонке google_country.
_GOOGLE_FORBIDDEN = frozenset(alpha3_to_alpha2(c) for c in GEMINI_FORBIDDEN)


def _ai_cell(test: str, ok, m: dict, error: str) -> dict:
    """Ячейка AI-теста: ok — галка/крест, unk — сетевой сбой (вердикт прежний),
    cc — страна по мнению Google (alpha-2, для флага)."""
    title = (error or "").strip()
    unk = bool(m.get("inconclusive"))                   # сеть: вердикт прежний
    cell = {"ok": 1 if ok else 0, "ai": 1}
    if unk:
        cell["unk"] = 1
    if test == "gemini":
        seen = [c for c in m.get("countries") or [] if c]
        bad = [c for c in seen if c in title]          # запрещённая страна названа в ошибке
        if bad:
            cc3 = bad[0]
        else:
            cc3 = m.get("country") or (seen[0] if seen else "")
        cell["cc"] = alpha3_to_alpha2(cc3)
        if ok:
            cell["v"] = cc3 or "OK"
        else:
            label = "Не определено" if unk else "Провал"
            cell["v"] = label + (f" [{', '.join(sorted(set(seen)))}]" if seen else "")
    elif ok:
        cell["v"] = "Доступен"
    elif unk:
        cell["v"] = "Не определено"
    else:
        cell["v"] = "Недоступен"
    if title:
        cell["title"] = title
    return cell


def _google_country_cell(ok, m: dict, error: str) -> dict:
    """Страна по мнению Google: флаг; крест — страна, где Google AI недоступен."""
    if not ok:
        return {"v": "Не определено", "ok": 0, "unk": 1, "ai": 1, "title": (error or "").strip()}
    cc = m.get("country") or ""
    return {"v": cc, "ok": 0 if cc in _GOOGLE_FORBIDDEN else 1, "ai": 1, "cc": cc}


def _whitelist_cell(ok, m: dict) -> dict:
    """Ячейка белых списков: галка или крест, подробности — адреса и SNI."""
    parts = [f"IP {', '.join(m.get('ips') or []) or '?'}: {'в списке' if m.get('ip_ok') else 'нет в списке'}"]
    if m.get("sni"):
        parts.append(f"SNI {m['sni']}: {'в списке' if m.get('sni_ok') else 'нет в списке'}")
    else:
        parts.append("без TLS — достаточно IP")
    return {"v": "Пройдена" if ok else "Не пройдена", "ok": 1 if ok else 0, "ai": 1, "title": "\n".join(parts)}


def _cell(test: str, ok, metrics_json: str, error: str = "") -> dict:
    """Компактное значение теста для ячейки: {v, ok, title?, cause?}. У провала — код
    причины (diagnose.CAUSES), в подсказке — и ошибка sing-box за время теста."""
    try:
        m = json.loads(metrics_json) if metrics_json else {}
    except (TypeError, ValueError):
        m = {}
    cell = _value_cell(test, ok, m, error)
    if not ok:
        from nodes_tester.diagnose import cause
        code = cause(test, m, error)
        if code:
            cell["cause"] = code
        if m.get("core_error"):
            core = "sing-box: " + m["core_error"]
            cell["title"] = f"{cell['title']}\n{core}" if cell.get("title") else core
    return cell


def _value_cell(test: str, ok, m: dict, error: str) -> dict:
    if test in _AI_TESTS:
        return _ai_cell(test, ok, m, error)
    if test == "whitelist":
        return _whitelist_cell(ok, m)
    if test == "google_country":
        return _google_country_cell(ok, m, error)
    if test == "connectivity":                # галка или крест: страна — во флаге строки
        cell = {"v": "Есть" if ok else "Провал", "ok": 1 if ok else 0, "ai": 1}
        if not ok and (error or "").strip():
            cell["title"] = error.strip()
        return cell
    if not ok:
        return {"v": "Провал", "ok": 0, "title": (error or "").strip()}
    if test == "latency":
        return {"v": f"{m.get('ttfb_ms', '?')}", "ok": 1}
    if test == "jitter":
        return {"v": f"{m.get('jitter_ms', '?')} / {m.get('loss_pct', 0)}", "ok": 1}
    if test == "download":
        # единый транспорт-тест: скорость + троттлинг (+ метка limited при 429)
        thr = m.get("throttle_ratio")
        tag = " (ограничение сервера)" if m.get("limited") else ""
        spd = f"{m.get('speed_mbps', '?')}"
        return {"v": (f"{spd} ×{thr}{tag}" if thr is not None else f"{spd}{tag}"), "ok": 1}
    if test == "reachability":
        return {"v": f"{m.get('reached', '?')}/{m.get('total', '?')}", "ok": 1}
    if test == "heavy_download":         # veto-тест кандидатов: скорость (FAIL=veto)
        return {"v": f"{m.get('speed_mbps', '?')}", "ok": 1}
    return {"v": "ok", "ok": 1}


_IP_KIND = {"hosting": "ДЦ", "mobile": "мобильный", "residential": "домашний"}


def _ip_type(node: dict, metrics_json: str, ip_info: dict) -> None:
    """Тип выходного IP в строку ноды (ip_type, ip_asn) и в подсказку ячейки connectivity."""
    try:
        m = json.loads(metrics_json) if metrics_json else {}
    except (TypeError, ValueError):
        return
    info = ip_info.get(m.get("exit_ip") or "")
    if not info:
        return
    from nodes_tester.entry import network
    from nodes_tester.ipinfo import db_country_mismatch, kind
    k = kind(info)
    node["ip_kind"] = k
    node["ip_proxy"] = 1 if info.get("proxy") else 0
    node["ip_db_cc"] = db_country_mismatch(info, m.get("country") or "")
    node["ip_type"] = _IP_KIND[k] + (", прокси" if node["ip_proxy"] else "")
    if node["ip_db_cc"]:
        node["ip_type"] += f", по базе {node['ip_db_cc']}"
    node["ip_asn"] = network(info)
    node["ip_ts"] = info.get("ts")
    node["ip_title"] = " · ".join(x for x in (node["ip_type"], m.get("exit_ip"), node["ip_asn"],
                                              info.get("org")) if x)


def _chains(db: str) -> dict:
    """Ключ ноды → вход и выход (nodes_tester.entry.chain) и строки для поиска и сортировки
    столбцов: chain — «FI→NL» только у цепочки, route — она же или страна выхода прямой ноды."""
    from nodes_tester.entry import chain, is_ip
    entry = {r["host"]: [ip for ip in (r["ips"] or "").split(",") if ip]
             for r in _query(db, "SELECT host, ips FROM entry_dns")}
    ip_info = {r["ip"]: r for r in _query(db, "SELECT ip, asn, as_name, isp, country FROM ip_info")}
    out = {}
    for r in _query(db, "SELECT s.crc AS crc, s.exit_ip AS exit_ip, s.exit_cc AS exit_cc, "
                        "n.server AS server FROM scores s JOIN nodes n ON n.crc = s.crc"):
        server = r["server"] or ""
        info = chain([server] if is_ip(server) else entry.get(server), r["exit_ip"], r["exit_cc"], ip_info)
        if not info:
            continue
        is_chain = info.pop("chain")
        if is_chain:
            info["chain"] = f"{info['entry_cc'] or '?'}→{info['exit_cc']}" + (" WARP" if info["warp"] else "")
        info["route"] = info.get("chain") or info["exit_cc"]
        out[r["crc"]] = info
    return out


def _results(db: str) -> dict:
    """Свод по ноде: последний результат каждого теста со временем замера. Служебная
    запись _select ('переключ.') в таблицу не выводится."""
    rows = _query(db, """
        SELECT r.crc AS crc, r.test AS test, r.ok AS ok, r.metrics AS metrics,
               r.error AS error, r.ts AS ts,
               n.provider AS provider, n.protocol AS protocol, n.country AS country
        FROM results r LEFT JOIN nodes n ON n.crc = r.crc
        WHERE r.test != '_select' ORDER BY r.ts, r.rowid""")
    ip_rows = _query(db, "SELECT ip, asn, as_name, isp, org, country, mobile, proxy, hosting, ts "
                         "FROM ip_info")
    ip_info = {r.get("ip"): r for r in ip_rows if r.get("ip")}
    latest: dict[str, dict] = {}
    connected: dict[str, int] = {}             # ключ ноды → время последнего соединения
    for r in rows:                             # по возрастанию времени: последний перекрывает
        latest.setdefault(r["crc"], {})[r["test"]] = r
        if r["test"] == "connectivity" and r["ok"]:
            connected[r["crc"]] = r["ts"]

    tests_seen: set[str] = set()
    node_rows: list[dict] = []
    for crc, by_test in latest.items():
        any_row = next(iter(by_test.values()))
        node = {"provider": any_row.get("provider"), "protocol": any_row.get("protocol"),
                "country": any_row.get("country"), "crc": crc,
                "ts": max(r["ts"] or 0 for r in by_test.values()), "cells": {}}
        for test, r in by_test.items():
            node["cells"][test] = {**_cell(test, r["ok"], r["metrics"], r.get("error")), "ts": r["ts"]}
            tests_seen.add(test)
        conn = by_test.get("connectivity")
        if conn and conn["ok"]:
            _ip_type(node, conn["metrics"], ip_info)
        elif node["country"]:
            # Последнее соединение не прошло — флаг из прошлого замера, админка показывает
            # его бледным с временем замера.
            node["country_stale"] = 1
            node["country_ts"] = connected.get(crc)
        node_rows.append(node)

    # Все проверки — всегда: столбец без результатов (новая база, проверка ещё не шла)
    # показывает прочерки, а не пропадает.
    cols = list(_TEST_ORDER) + sorted(tests_seen - set(_TEST_ORDER))
    node_rows.sort(key=lambda x: x["ts"], reverse=True)
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
    rows = _query(db, """
        SELECT g.crc AS crc, g.since AS since, g.until AS until, g.reason AS state,
               g.streak AS streak,
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
               s.score AS score, s.active AS active, s.reserve AS reserve, g.reason AS gstate
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
        if _float(r.get("score")) > 0 and not r.get("gstate") and not r.get("banned"):
            longevity.append({**base, "age": max(0, now - fs),
                              "score": r.get("score"), "active": r.get("active") or 0,
                              "reserve": r.get("reserve") or ""})
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
        SELECT n.crc AS crc, n.tag AS tag, n.title AS title, n.provider AS provider,
               n.protocol AS protocol, n.country AS cc,
               n.first_seen AS first_seen, n.last_seen AS last_seen,
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
            "crc": r["crc"], "tag": r.get("tag"), "title": r.get("title"),
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
    """Ноды подписок (таблица nodes) для раздела «Все ноды» админки. Ушедшие из подписок
    (present = 0) — только в «Архиве»; present ещё не известен у ноды, которую конвейер
    только что собрал, — она показывается.

    Для каждой ноды — идентичность (crc/tag/provider/protocol/country/server),
    флаги present/banned, счёт рейтинга (score/region/active из scores) и текущий
    backoff/карантин (из garbage). Бан (nodes.banned) вносится админкой (api_control).
    """
    rows = _query(db, """
        SELECT n.crc AS crc, n.tag AS node, n.title AS title, n.provider AS provider,
               n.protocol AS protocol, n.country AS country, n.server AS server,
               n.present AS present, COALESCE(n.banned, 0) AS banned,
               s.score AS score, s.region AS region, s.active AS active, s.reserve AS reserve,
               g.reason AS gstate, g.until AS guntil, g.streak AS gstreak
        FROM nodes n
        LEFT JOIN scores s ON s.crc = n.crc
        LEFT JOIN garbage g ON g.crc = n.crc
        WHERE n.present IS NOT 0
        ORDER BY n.present DESC, s.score DESC""")
    return rows


def _float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
