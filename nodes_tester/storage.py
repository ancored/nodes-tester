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

Одно соединение sqlite3 (check_same_thread=False) + Lock: пишут поток прогона и
поток сбора трафика. Режим WAL для параллельного чтения.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from naming import content_crc32, node_payload, parse_node

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

    # --- Обслуживание --------------------------------------------------

    def prune(self) -> None:
        cutoff = int(time.time()) - int(self.cfg.retention_days) * 86400
        with self._lock:
            self._db.execute("DELETE FROM traffic WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM results WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM endpoints WHERE last_seen < ?", (cutoff,))
            self._db.commit()
            self._db.execute("VACUUM")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
