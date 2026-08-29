"""SQLite-хранилище: описание нод + трафик + сырые результаты тестов.

Всё связано по CRC ноды (8-hex из тега `...-out [CRC]`). CRC — стабильный
fingerprint настроек ноды (его считает sing-box-subscribe), поэтому история
переживает переименования тегов.

Таблицы:
  nodes(crc PK, tag, provider/protocol/country, type, server/port, payload JSON,
        crc_ok, first_seen, last_seen)   — описание ноды (все поля, из которых CRC)
  traffic(ts, crc, up, down, conns, is_tester)                 — временной ряд объёма
  endpoints(crc, source_ip, dest_host, network, up, down, flows, last_seen)
  results(ts, pass_no, crc, test, ok, url, metrics JSON, error)
  activations(ts, region, crc, tag, reason, score, prev)       — история переключений

Одно соединение sqlite3 (check_same_thread=False) + Lock: пишут поток прогона и
поток сбора трафика. Режим WAL для параллельного чтения.

Очистка (cleanup) — единая и согласованная: удаляем ноды с last_seen старше
retention_days и КАСКАДОМ все их строки во всех таблицах (по crc). VACUUM —
отдельно (vacuum(), под cron).
"""

from __future__ import annotations

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
  first_seen INTEGER, last_seen INTEGER);
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
  crc TEXT PRIMARY KEY, since INTEGER, until INTEGER, reason TEXT);
"""

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
        try:                                   # миграция старых БД: колонка label
            self._db.execute("ALTER TABLE nodes ADD COLUMN label TEXT")
        except sqlite3.OperationalError:
            pass                               # колонка уже есть
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
        outbounds = data.get("outbounds", data) if isinstance(data, dict) else data
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

    # --- Мусорные ноды (провалили gate дольше max_skip → карантин на N часов) ---
    # Переживает рестарт: иначе после перезапуска мёртвую ноду снова тестировали бы
    # (лишние обращения к заблокированным серверам — дразним ТСПУ).

    def mark_garbage(self, crc: str, until: int, reason: str = "max_skip") -> None:
        """Пометить ноду мусорной до момента until (unix ts): исключена из тестов."""
        if not crc:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO garbage (crc, since, until, reason) VALUES (?,?,?,?) "
                "ON CONFLICT(crc) DO UPDATE SET until=excluded.until, reason=excluded.reason",
                (crc, int(time.time()), int(until), reason))
            self._db.commit()

    def clear_garbage(self, crc: str) -> None:
        """Снять карантин (нода снова прошла gate)."""
        if not crc:
            return
        with self._lock:
            self._db.execute("DELETE FROM garbage WHERE crc = ?", (crc,))
            self._db.commit()

    def active_garbage(self) -> dict:
        """{crc: until} для нод, чей карантин ещё НЕ истёк (снимок на начало прогона)."""
        now = int(time.time())
        with self._lock:
            rows = self._db.execute(
                "SELECT crc, until FROM garbage WHERE until > ?", (now,)).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def all_garbage(self) -> dict:
        """{crc: until} по ВСЕМ строкам карантина (истёкшие until<=now — «на пробе»)."""
        with self._lock:
            rows = self._db.execute("SELECT crc, until FROM garbage").fetchall()
        return {r[0]: int(r[1]) for r in rows}

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
                for tbl in ("activations", "endpoints", "results", "traffic", "garbage", "nodes"):
                    self._db.execute(f"DELETE FROM {tbl} WHERE crc IN ({ph})", stale)
            # истёкший карантин (until в прошлом) — нода снова тестируема, строка не нужна
            self._db.execute("DELETE FROM garbage WHERE until < ?", (int(time.time()),))
            # 2) возрастной кап НЕ-нодовых строк (direct-out, нераспознанные группы):
            #    к ноде не привязаны, каскад их не трогает — режем по возрасту, чтобы
            #    не пухли. Свежие (< cutoff) оставляем (нужны витрине трафика).
            self._db.execute(
                "DELETE FROM traffic WHERE ts < ? "
                "AND crc NOT IN (SELECT crc FROM nodes)", (cutoff,))
            self._db.execute(
                "DELETE FROM endpoints WHERE last_seen < ? "
                "AND crc NOT IN (SELECT crc FROM nodes)", (cutoff,))
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
