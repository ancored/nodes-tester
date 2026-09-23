"""SQLite-хранилище: описание нод + трафик + сырые результаты тестов.

Всё связано по CRC ноды (8-hex из тега `...-out [CRC]`). CRC — стабильный
fingerprint настроек ноды (его считает sing-box-subscribe), поэтому история
переживает переименования тегов.

Таблицы (полное описание — SCHEMA.md в корне репо):
  nodes(crc PK, tag, provider/protocol/country/label, type, server/port, payload JSON,
        crc_ok, first_seen, last_seen, present)   — описание ноды + флаг присутствия
  traffic(ts, crc, up, down, conns, is_tester)                 — временной ряд объёма
  endpoints(crc, source_ip, dest_host, network, up, down, flows, last_seen)
  results(ts, pass_no, crc, test, ok, url, metrics JSON, error)
  activations(ts, region, crc, tag, reason, score, prev)       — история переключений
  scores(crc PK, …)                — снимок рейтинга (замена score.csv)
  score_history(ts, crc, region, score, s_run, gate)           — динамика рейтинга
  switch_state / switch_recent / switch_activations            — состояние switcher
  node_events(ts, crc, event, reason, streak)  — журнал added/removed/backoff/garbage/recovered
  garbage(crc PK, since, until, reason, streak) — текущий backoff/карантин
  meta(key PK, value)              — сквозные значения между рестартами

Одно соединение sqlite3 (check_same_thread=False) + Lock: пишут поток прогона и
поток сбора трафика. Режим WAL для параллельного чтения.

Очистка (cleanup) — единая и согласованная: удаляем ноды с last_seen старше
retention_days и КАСКАДОМ все их строки во всех таблицах (по crc). VACUUM —
отдельно (vacuum(), под cron).
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import time

from naming import content_crc32, node_payload, parse_group, parse_node

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  crc TEXT PRIMARY KEY, tag TEXT, provider TEXT, protocol TEXT, country TEXT, label TEXT,
  type TEXT, server TEXT, server_port INTEGER, payload TEXT, crc_ok INTEGER,
  first_seen INTEGER, last_seen INTEGER, present INTEGER, banned INTEGER);
CREATE TABLE IF NOT EXISTS traffic (
  ts INTEGER, crc TEXT, up INTEGER, down INTEGER, conns INTEGER, is_tester INTEGER);
CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic(ts);
CREATE INDEX IF NOT EXISTS idx_traffic_crc ON traffic(crc);
CREATE TABLE IF NOT EXISTS endpoints (
  crc TEXT, source_ip TEXT, dest_host TEXT, network TEXT,
  up INTEGER, down INTEGER, flows INTEGER, last_seen INTEGER,
  PRIMARY KEY (crc, source_ip, dest_host, network));
CREATE TABLE IF NOT EXISTS results (
  ts INTEGER, pass_no INTEGER, crc TEXT, test TEXT, ok INTEGER,
  url TEXT, metrics TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS idx_results_ts ON results(ts);
CREATE INDEX IF NOT EXISTS idx_results_crc ON results(crc);
CREATE TABLE IF NOT EXISTS activations (
  ts INTEGER, region TEXT, crc TEXT, tag TEXT, reason TEXT, score REAL, prev TEXT);
CREATE INDEX IF NOT EXISTS idx_activations_ts ON activations(ts);
CREATE INDEX IF NOT EXISTS idx_activations_crc ON activations(crc);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS garbage (
  crc TEXT PRIMARY KEY, since INTEGER, until INTEGER, reason TEXT, streak INTEGER);
CREATE TABLE IF NOT EXISTS scores (
  crc TEXT PRIMARY KEY, node TEXT, provider TEXT, protocol TEXT, region TEXT,
  country TEXT, label TEXT, active INTEGER, score REAL,
  reliability REAL, consistency REAL, throttle REAL, jitter REAL, latency REAL, throughput REAL,
  score_ewma REAL, avail REAL, flap REAL, samples INTEGER, last_pass INTEGER,
  heavy_ok TEXT, heavy_ts INTEGER);
CREATE TABLE IF NOT EXISTS score_history (
  ts INTEGER, crc TEXT, region TEXT, score REAL, s_run REAL, gate INTEGER);
CREATE INDEX IF NOT EXISTS idx_score_history_ts ON score_history(ts);
CREATE INDEX IF NOT EXISTS idx_score_history_crc ON score_history(crc);
CREATE TABLE IF NOT EXISTS switch_state (
  region TEXT PRIMARY KEY, active TEXT, last_switch REAL, rotate_deadline REAL,
  quality_count INTEGER, emg_stuck INTEGER);
CREATE TABLE IF NOT EXISTS switch_recent (
  region TEXT, seq INTEGER, node TEXT, PRIMARY KEY (region, seq));
CREATE TABLE IF NOT EXISTS switch_activations (
  region TEXT, node TEXT, count INTEGER, PRIMARY KEY (region, node));
CREATE TABLE IF NOT EXISTS node_events (
  ts INTEGER, crc TEXT, event TEXT, reason TEXT, streak INTEGER);
CREATE INDEX IF NOT EXISTS idx_node_events_ts ON node_events(ts);
CREATE INDEX IF NOT EXISTS idx_node_events_crc ON node_events(crc);
"""

# Колонки снимка рейтинга (таблица scores). Ключ БД — crc; в памяти Scoreboard
# ключует по raw-тегу (node), поэтому node храним отдельной колонкой.
_SCORE_COLS = (
    "node", "provider", "protocol", "region", "country", "label", "active", "score",
    "reliability", "consistency", "throttle", "jitter", "latency", "throughput",
    "score_ewma", "avail", "flap", "samples", "last_pass", "heavy_ok", "heavy_ts",
)

# Типы outbound-групп, которые не являются нодами (не пишем в nodes).
_NON_NODE_TYPES = {"selector", "urltest", "direct", "block", "dns"}
_SERVICE_KEYS = {"ok", "url", "error"}


class Storage:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(os.path.dirname(os.path.abspath(cfg.db_file)), exist_ok=True)
        self._db = sqlite3.connect(cfg.db_file, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        for tbl, col, decl in (("nodes", "label", "TEXT"),
                                ("nodes", "present", "INTEGER"),
                                ("nodes", "banned", "INTEGER"),
                                ("garbage", "streak", "INTEGER")):
            try:                               # миграция старых БД (колонка могла отсутствовать)
                self._db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass                           # колонка уже есть
        self._db.commit()
        self._lock = threading.Lock()
        self._nodes_mtime = None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- Таблица nodes -------------------------------------------------

    def maybe_load_nodes(self, path: str) -> None:
        """Перечитать описания нод, если файл появился/изменился."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime == self._nodes_mtime:
            return
        self._nodes_mtime = mtime
        self.load_nodes(path)

    def load_nodes(self, path: str) -> int:
        """Загрузить полные описания leaf-нод из nodes.json (выход subscribe)."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  [storage] не удалось прочитать {path}: {exc}")
            return 0
        # sing-box 1.11+ вынес wireguard/amneziawg (AWG) из outbounds в отдельный
        # массив endpoints — читаем оба, иначе AWG-ноды теряются (нет строки в nodes
        # → в дашборде пустые provider/protocol при живом CRC из traffic/results).
        if isinstance(data, dict):
            outbounds = list(data.get("outbounds") or []) + list(data.get("endpoints") or [])
        else:
            outbounds = data or []
        now = int(time.time())
        rows = []
        for ob in outbounds or []:
            if not isinstance(ob, dict):
                continue
            tag = ob.get("tag", "")
            ident = parse_node(tag)
            if not ident.node_id:                       # нет CRC — группа/непарсимое
                continue
            if str(ob.get("type", "")).lower() in _NON_NODE_TYPES:
                continue
            crc_ok = 1 if content_crc32(ob) == ident.node_id else 0
            rows.append((
                ident.node_id, tag, ident.provider, ident.protocol, ident.country,
                ident.label, ob.get("type", ""), ob.get("server", ""), _int(ob.get("server_port")),
                node_payload(ob), crc_ok, now, now,   # node_payload — каноничная строка (основа CRC)
            ))
        with self._lock:
            self._db.executemany("""
                INSERT INTO nodes (crc,tag,provider,protocol,country,label,type,server,
                                   server_port,payload,crc_ok,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(crc) DO UPDATE SET
                  tag=excluded.tag, provider=excluded.provider, protocol=excluded.protocol,
                  country=excluded.country, label=excluded.label, type=excluded.type,
                  server=excluded.server, server_port=excluded.server_port,
                  payload=excluded.payload, crc_ok=excluded.crc_ok, last_seen=excluded.last_seen
            """, rows)
            self._db.commit()
        bad = sum(1 for r in rows if not r[10])   # crc_ok
        print(f"  [storage] нод загружено: {len(rows)}"
              + (f" (CRC не сошёлся у {bad})" if bad else ""))
        return len(rows)

    def touch_seen(self, crcs) -> None:
        """Обновить last_seen для нод, реально присутствующих в selector в этом прогоне.
        Иначе last_seen обновляется только при смене mtime nodes.json, и cleanup() через
        retention_days удалит активно тестируемую ноду вместе со всей историей
        (см. review.md P0). Обновляем присутствие, а не только факт замера."""
        seen = [c for c in {c for c in crcs if c}]
        if not seen:
            return
        now = int(time.time())
        with self._lock:
            self._db.executemany("UPDATE nodes SET last_seen = ? WHERE crc = ?",
                                 [(now, c) for c in seen])
            self._db.commit()

    def endpoints_by_crc(self) -> dict:
        """{crc: (server, server_port)} — хост+порт ноды для host-aware обхода тестера
        (анти-ТСПУ раскладка очереди и зазор между обращениями к одному хосту)."""
        with self._lock:
            rows = self._db.execute("SELECT crc, server, server_port FROM nodes").fetchall()
        return {r[0]: (r[1] or "", r[2]) for r in rows}

    # --- Результаты тестов ---------------------------------------------

    def add_results(self, record: dict) -> None:
        crc = record.get("id") or record.get("node")
        ts = int(time.time())
        rows = []
        for test, res in (record.get("tests") or {}).items():
            metrics = {k: v for k, v in res.items() if k not in _SERVICE_KEYS}
            rows.append((ts, record.get("round"), crc, test,
                         1 if res.get("ok") else 0, res.get("url", ""),
                         json.dumps(metrics, ensure_ascii=False), res.get("error", "")))
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO results (ts,pass_no,crc,test,ok,url,metrics,error) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)
            self._db.commit()

    # --- Трафик --------------------------------------------------------

    def add_traffic(self, ts: int, rows: list) -> None:
        """rows: [(crc, up, down, conns, is_tester), ...]"""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO traffic (ts,crc,up,down,conns,is_tester) "
                "VALUES (?,?,?,?,?,?)", [(ts, *r) for r in rows])
            self._db.commit()

    def add_traffic_batch(self, ts: int, node_rows: list, ep_rows: list) -> None:
        """traffic + endpoints ОДНОЙ транзакцией (один commit) — чтобы частичная запись
        не рассинхронизировала витрину и не теряла дельты (см. review.md P1)."""
        if not node_rows and not ep_rows:
            return
        with self._lock:
            if node_rows:
                self._db.executemany(
                    "INSERT INTO traffic (ts,crc,up,down,conns,is_tester) "
                    "VALUES (?,?,?,?,?,?)", [(ts, *r) for r in node_rows])
            if ep_rows:
                self._db.executemany("""
                    INSERT INTO endpoints (crc,source_ip,dest_host,network,up,down,flows,last_seen)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(crc,source_ip,dest_host,network) DO UPDATE SET
                      up=up+excluded.up, down=down+excluded.down,
                      flows=flows+excluded.flows, last_seen=excluded.last_seen
                """, [(*r, ts) for r in ep_rows])
            self._db.commit()

    def upsert_endpoints(self, ts: int, rows: list) -> None:
        """rows: [(crc, source_ip, dest_host, network, up, down, flows), ...]"""
        if not rows:
            return
        with self._lock:
            self._db.executemany("""
                INSERT INTO endpoints (crc,source_ip,dest_host,network,up,down,flows,last_seen)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(crc,source_ip,dest_host,network) DO UPDATE SET
                  up=up+excluded.up, down=down+excluded.down,
                  flows=flows+excluded.flows, last_seen=excluded.last_seen
            """, [(*r, ts) for r in rows])
            self._db.commit()

    def traffic_by_dims(self, window_seconds: float) -> dict:
        """Боевой трафик (up+down) за окно, агрегированный по осям балансировки:
        {'provider':{...}, 'country':{...}, 'protocol':{...}}.

        Leaf-трафик берём из JOIN nodes (есть provider/country/protocol).
        Failsafe/unspecified-трафик (ключ = тег группы, не в nodes) добираем в
        провайдера через parse_group; страны/протокола в имени группы нет — там
        мелкий недоучёт по этим осям (приемлемо). is_tester=0 — тестовый не считаем.
        """
        since = int(time.time()) - int(window_seconds)
        prov: dict = {}
        cc: dict = {}
        proto: dict = {}
        with self._lock:
            for p, c, pr, b in self._db.execute(
                "SELECT n.provider, n.country, n.protocol, SUM(t.up+t.down) "
                "FROM traffic t JOIN nodes n ON n.crc = t.crc "
                "WHERE t.is_tester = 0 AND t.ts >= ? "
                "GROUP BY n.provider, n.country, n.protocol", (since,)).fetchall():
                b = int(b or 0)
                if p:
                    prov[p] = prov.get(p, 0) + b
                if c:
                    cc[c] = cc.get(c, 0) + b
                if pr:
                    proto[pr] = proto.get(pr, 0) + b
            # не-нодовые ключи (failsafe-группы) → провайдер через parse_group
            for crc, b in self._db.execute(
                "SELECT t.crc, SUM(t.up+t.down) FROM traffic t "
                "LEFT JOIN nodes n ON n.crc = t.crc "
                "WHERE t.is_tester = 0 AND t.ts >= ? AND n.crc IS NULL "
                "GROUP BY t.crc", (since,)).fetchall():
                pg = parse_group(crc or "")
                if pg:
                    prov[pg[0]] = prov.get(pg[0], 0) + int(b or 0)
        return {"provider": prov, "country": cc, "protocol": proto}

    # --- Мета (сквозные значения между рестартами) ---------------------

    def get_meta(self, key: str, default=None):
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
            self._db.commit()

    def max_pass_today(self, day_str: str) -> int:
        """Макс. pass_no среди результатов за указанный локальный день (YYYY-MM-DD).
        Нужен при первом запуске после апдейта (метки pass_no ещё нет), чтобы не
        начинать сегодняшнюю нумерацию с 1 поверх уже записанных прогонов."""
        try:
            start = int(time.mktime(time.strptime(day_str, "%Y-%m-%d")))
        except ValueError:
            return 0
        end = start + 86400
        with self._lock:
            r = self._db.execute(
                "SELECT MAX(pass_no) FROM results WHERE ts >= ? AND ts < ?",
                (start, end)).fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    # --- Backoff / карантин нод (единая ВРЕМЕННАЯ модель, персистентная) --------
    # Нода, провалившая gate, не тестируется до `until`; `streak` — число подряд
    # провалов (для удвоения). Переживает рестарт и измеряется временем, а не
    # номерами прогонов (совместимо с rotation_bound). reason: backoff | garbage.

    def load_backoff(self) -> dict:
        """{crc: (until, streak, reason)} по всем строкам backoff (снимок на прогон)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT crc, until, streak, reason FROM garbage").fetchall()
        return {r[0]: (int(r[1] or 0), int(r[2] or 0), r[3] or "") for r in rows}

    def set_backoff(self, crc: str, until: int, streak: int, reason: str) -> None:
        """Записать/обновить backoff ноды до момента until (unix ts)."""
        if not crc:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO garbage (crc, since, until, reason, streak) VALUES (?,?,?,?,?) "
                "ON CONFLICT(crc) DO UPDATE SET until=excluded.until, "
                "reason=excluded.reason, streak=excluded.streak",
                (crc, int(time.time()), int(until), reason, int(streak)))
            self._db.commit()

    def clear_backoff(self, crc: str) -> None:
        """Снять backoff/карантин (нода снова прошла gate)."""
        if not crc:
            return
        with self._lock:
            self._db.execute("DELETE FROM garbage WHERE crc = ?", (crc,))
            self._db.commit()

    # --- Ручной бан ноды (из админки) ----------------------------------
    # Бан — независимый от backoff флаг nodes.banned: забаненная нода пропускается
    # Runner._enumerate_nodes (не тестируется, не скорится), пока флаг не снят.

    def node_exists(self, crc: str) -> bool:
        """Есть ли нода с таким CRC в каталоге nodes."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM nodes WHERE crc = ? LIMIT 1", (crc,)).fetchone()
        return row is not None

    def set_banned(self, crc: str, banned: bool) -> None:
        """Поставить/снять флаг бана ноды (персистентно, переживает рестарт)."""
        if not crc:
            return
        with self._lock:
            self._db.execute("UPDATE nodes SET banned = ? WHERE crc = ?",
                             (1 if banned else 0, crc))
            self._db.commit()

    def banned_crcs(self) -> set:
        """Множество CRC забаненных нод (снимок на прогон)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT crc FROM nodes WHERE banned = 1").fetchall()
        return {r[0] for r in rows}

    # --- История активаций --------------------------------------------

    def add_activation(self, region: str, crc: str, tag: str, reason: str,
                       score, prev: "str | None" = None) -> None:
        """Записать факт активации ноды в регионе (из switcher._activate)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO activations (ts,region,crc,tag,reason,score,prev) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(time.time()), region, crc or "", tag, reason,
                 float(score or 0), prev))
            self._db.commit()

    # --- Рейтинг (таблица scores — замена score.csv) -------------------

    def load_scores(self) -> dict:
        """{node_tag: row} — снимок рейтинга. Ключи row совпадают с тем, что Scoreboard
        раньше грузил из csv (in-memory ключ — raw-тег node; crc отдаётся как 'id')."""
        with self._lock:
            rows = self._db.execute(
                "SELECT crc,node,provider,protocol,region,country,label,active,score,"
                "reliability,consistency,throttle,jitter,latency,throughput,"
                "score_ewma,avail,flap,samples,last_pass,heavy_ok,heavy_ts FROM scores"
            ).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            tag = r[1] or r[0]
            out[tag] = {
                "node": tag, "provider": r[2] or "", "protocol": r[3] or "",
                "region": r[4] or "", "country": r[5] or "", "label": r[6] or "",
                "id": r[0], "active": _int(r[7]), "score": _flt(r[8]),
                "reliability": _flt(r[9]), "consistency": _flt(r[10]),
                "throttle": _flt(r[11]), "jitter": _flt(r[12]),
                "latency": _flt(r[13]), "throughput": _flt(r[14]),
                "score_ewma": _flt(r[15]), "avail": _flt(r[16]), "flap": _flt(r[17]),
                "samples": _int(r[18]), "last_seen": _int(r[19]),
                "heavy_ok": "" if r[20] is None else str(r[20]), "heavy_ts": _int(r[21]),
            }
        return out

    def save_scores(self, rows) -> None:
        """Полная перезапись снимка рейтинга (исчезнувшие ноды уходят). Ключ БД — crc.

        Разные теги с ОДИНАКОВЫМ crc (нода-дубль: идентичный конфиг, разное имя) в памяти
        Scoreboard — отдельные строки, но в scores crc это PRIMARY KEY. Дедуплицируем по
        crc, оставляя предпочтительную (активная > больший score), иначе INSERT упал бы с
        UNIQUE constraint failed: scores.crc."""
        by_crc: dict[str, tuple] = {}
        for r in rows:
            crc = r.get("id") or r.get("node")
            if not crc:
                continue
            rank = ((_int(r.get("active")) or 0), _flt(r.get("score")))
            row = (
                crc, r.get("node", ""), r.get("provider", ""), r.get("protocol", ""),
                r.get("region", ""), r.get("country", ""), r.get("label", ""),
                _int(r.get("active")), _flt(r.get("score")),
                _flt(r.get("reliability")), _flt(r.get("consistency")),
                _flt(r.get("throttle")), _flt(r.get("jitter")),
                _flt(r.get("latency")), _flt(r.get("throughput")),
                _flt(r.get("score_ewma")), _flt(r.get("avail")), _flt(r.get("flap")),
                _int(r.get("samples")), _int(r.get("last_seen")),
                "" if r.get("heavy_ok") in (None,) else str(r.get("heavy_ok")),
                _int(r.get("heavy_ts")),
            )
            prev = by_crc.get(crc)
            if prev is None or rank > prev[0]:
                by_crc[crc] = (rank, row)
        packed = [v[1] for v in by_crc.values()]
        with self._lock:
            self._db.execute("DELETE FROM scores")
            if packed:
                self._db.executemany(
                    "INSERT INTO scores (crc,node,provider,protocol,region,country,label,"
                    "active,score,reliability,consistency,throttle,jitter,latency,throughput,"
                    "score_ewma,avail,flap,samples,last_pass,heavy_ok,heavy_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", packed)
            self._db.commit()

    def set_active_crc(self, region: str, crc: "str | None") -> None:
        """Пометить активную ноду региона в таблице scores (active=1 у crc, 0 у прочих
        того же региона). Вызывается из switcher при переключении: save_scores полностью
        переписывает scores лишь раз в прогон (end_pass), а переключения (ротация/emergency
        из фонового монитора) идут между прогонами — без этого scores.active в БД отстаёт
        от activations, и дашборд показывает разные активные ноды в двух таблицах."""
        with self._lock:
            self._db.execute("UPDATE scores SET active = 0 WHERE region = ?", (region,))
            if crc:
                self._db.execute(
                    "UPDATE scores SET active = 1 WHERE region = ? AND crc = ?",
                    (region, crc))
            self._db.commit()

    def add_score_history(self, ts: int, rows) -> None:
        """rows: [(crc, region, score, s_run, gate), ...] — точки истории рейтинга."""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO score_history (ts,crc,region,score,s_run,gate) VALUES (?,?,?,?,?,?)",
                [(ts, c, rg, float(sc or 0), float(sr or 0), 1 if g else 0)
                 for (c, rg, sc, sr, g) in rows])
            self._db.commit()

    # --- Состояние переключений (таблицы switch_* — замена switch_state.json) ---

    def load_switch_state(self) -> dict:
        """{region: {active,last_switch,rotate_deadline,quality_count,recent[],activations{},emg_stuck?}}."""
        state: dict[str, dict] = {}
        with self._lock:
            for region, active, ls, rd, qc, emg in self._db.execute(
                "SELECT region,active,last_switch,rotate_deadline,quality_count,emg_stuck "
                "FROM switch_state"):
                st = {"active": active, "last_switch": _flt(ls), "rotate_deadline": _flt(rd),
                      "quality_count": _int(qc), "recent": [], "activations": {}}
                if emg:
                    st["emg_stuck"] = True
                state[region] = st
            for region, _seq, node in self._db.execute(
                "SELECT region,seq,node FROM switch_recent ORDER BY region, seq"):
                if region in state:
                    state[region]["recent"].append(node)
            for region, node, cnt in self._db.execute(
                "SELECT region,node,count FROM switch_activations"):
                if region in state:
                    state[region]["activations"][node] = _int(cnt)
        return state

    def save_switch_state(self, state: dict) -> None:
        """Полная перезапись состояния переключений (регионов немного)."""
        with self._lock:
            self._db.execute("DELETE FROM switch_state")
            self._db.execute("DELETE FROM switch_recent")
            self._db.execute("DELETE FROM switch_activations")
            for region, st in (state or {}).items():
                self._db.execute(
                    "INSERT INTO switch_state (region,active,last_switch,rotate_deadline,"
                    "quality_count,emg_stuck) VALUES (?,?,?,?,?,?)",
                    (region, st.get("active"), float(st.get("last_switch", 0) or 0),
                     float(st.get("rotate_deadline", 0) or 0),
                     int(st.get("quality_count", 0) or 0), 1 if st.get("emg_stuck") else 0))
                for seq, node in enumerate(st.get("recent", []) or []):
                    self._db.execute("INSERT INTO switch_recent (region,seq,node) VALUES (?,?,?)",
                                     (region, seq, node))
                for node, cnt in (st.get("activations", {}) or {}).items():
                    self._db.execute(
                        "INSERT INTO switch_activations (region,node,count) VALUES (?,?,?)",
                        (region, node, int(cnt or 0)))
            self._db.commit()

    # --- Журнал событий ноды (added/removed/backoff/garbage/recovered) ---

    def add_node_event(self, crc: str, event: str, reason: str = "", streak: int = 0) -> None:
        """Записать событие жизненного цикла/здоровья ноды (пер-нодный таймлайн)."""
        if not crc:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO node_events (ts,crc,event,reason,streak) VALUES (?,?,?,?,?)",
                (int(time.time()), crc, event, reason or "", int(streak or 0)))
            self._db.commit()

    def reconcile_presence(self, crcs) -> tuple[list, list]:
        """Сверить текущее присутствие нод в selector с сохранённым флагом present:
        залогировать 'added' (появилась) и 'removed' (ушла из подписки) на КАЖДОМ
        переходе и обновить флаг. Возвращает (added, removed) списки crc.

        Присутствие ведём по нодам, у которых есть строка в nodes (реальные ноды из
        подписки); одна нода может появляться/выбывать многократно — каждое пишем."""
        now_set = {c for c in crcs if c}
        ts = int(time.time())
        with self._lock:
            prev = {r[0] for r in self._db.execute(
                "SELECT crc FROM nodes WHERE present = 1")}
            known = {r[0] for r in self._db.execute("SELECT crc FROM nodes")}
            added = [c for c in now_set if c not in prev and c in known]
            removed = [c for c in prev if c not in now_set]
            for c in added:
                self._db.execute(
                    "INSERT INTO node_events (ts,crc,event,reason,streak) VALUES (?,?,?,?,?)",
                    (ts, c, "added", "", 0))
            for c in removed:
                self._db.execute(
                    "INSERT INTO node_events (ts,crc,event,reason,streak) VALUES (?,?,?,?,?)",
                    (ts, c, "removed", "", 0))
            present_now = [c for c in now_set if c in known]
            if present_now:
                ph = ",".join("?" * len(present_now))
                self._db.execute(f"UPDATE nodes SET present = 1 WHERE crc IN ({ph})",
                                 present_now)
            if removed:
                ph2 = ",".join("?" * len(removed))
                self._db.execute(f"UPDATE nodes SET present = 0 WHERE crc IN ({ph2})", removed)
            self._db.commit()
        return added, removed

    # --- Однократная миграция старых файлов -----------------------------

    def migrate_legacy(self, score_csv: str, switch_json: str) -> None:
        """Импортировать старые score.csv / switch_state.json в БД, если таблицы пусты и
        файлы существуют (переход с файлового хранения). После — файлы не используются."""
        if score_csv and os.path.exists(score_csv) and self._table_empty("scores"):
            rows = _read_score_csv(score_csv)
            if rows:
                self.save_scores(rows)
                print(f"  [storage] миграция: рейтинг из {score_csv} ({len(rows)} нод)")
        if switch_json and os.path.exists(switch_json) and self._table_empty("switch_state"):
            try:
                with open(switch_json, encoding="utf-8") as fh:
                    state = json.load(fh)
            except (OSError, ValueError):
                state = {}
            if isinstance(state, dict) and state:
                self.save_switch_state(state)
                print(f"  [storage] миграция: состояние переключений из {switch_json}")

    def _table_empty(self, table: str) -> bool:
        with self._lock:
            n = self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return int(n or 0) == 0

    # --- Обслуживание --------------------------------------------------

    def cleanup(self) -> None:
        """Единый процесс очистки БД: удалить ноды, не встречавшиеся дольше
        retention_days, и КАСКАДОМ все их строки во ВСЕХ таблицах (по crc) —
        согласованно, без сирот (не бывает, что нода удалена из nodes, а её
        активации/трафик остались). Историю ЖИВЫХ нод не трогаем (храним БД).

        VACUUM здесь НЕ выполняется — он отдельным процессом (vacuum(), под cron).
        """
        cutoff = int(time.time()) - int(self.cfg.retention_days) * 86400
        with self._lock:
            # 1) устаревшие ноды → каскад по всем таблицам (по crc, без сирот)
            stale = [r[0] for r in self._db.execute(
                "SELECT crc FROM nodes WHERE last_seen < ?", (cutoff,)).fetchall()]
            if stale:
                ph = ",".join("?" * len(stale))
                # дочерние строки раньше родителя (порядок логический; FK нет)
                for tbl in ("activations", "endpoints", "results", "traffic", "garbage",
                            "score_history", "node_events", "scores", "nodes"):
                    self._db.execute(f"DELETE FROM {tbl} WHERE crc IN ({ph})", stale)
            # давно истёкший backoff (старше retention) — подчистить, чтобы не пух;
            # свежий истёкший оставляем: его streak нужен для продолжения серии при
            # следующей пробе, если нода снова провалит gate.
            self._db.execute("DELETE FROM garbage WHERE until < ?", (cutoff,))
            # 2) ВОЗРАСТНОЙ КАП сырых фактов — и у ЖИВЫХ нод тоже. Иначе results/traffic/
            #    activations/endpoints растут без предела (дашборд агрегирует всё, БД пухнет
            #    на роутере). Храним только последние retention_days сырья (см. review.md P2).
            self._db.execute("DELETE FROM results WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM traffic WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM activations WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM score_history WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM node_events WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM endpoints WHERE last_seen < ?", (cutoff,))
            self._db.commit()
        if stale:
            print(f"  [storage] очистка: удалено устаревших нод {len(stale)} "
                  f"(+ их трафик/результаты/эндпоинты/активации)")

    def vacuum(self) -> None:
        """Отдельный VACUUM (переупаковка БД). Тяжёлый — запускать раз в месяц
        по cron: `python -m nodes_tester --vacuum -c <config>`.

        busy_timeout: тестер-сервис держит свою connection и пишет (WAL), поэтому
        даём VACUUM подождать окна эксклюзивного доступа, а не падать с locked."""
        with self._lock:
            self._db.execute("PRAGMA busy_timeout = 60000")   # ждать активного писателя
            self._db.execute("VACUUM")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _flt(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _read_score_csv(path: str) -> list[dict]:
    """Старый score.csv → список row-словарей (для однократной миграции)."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh) if r.get("node")]
    except (OSError, ValueError):
        return []
