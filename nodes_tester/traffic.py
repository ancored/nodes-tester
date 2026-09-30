"""Точный учёт трафика по нодам: поток SubscribeConnections API-сервиса sing-box → SQLite.

Фоновый поток держит подписку на события соединений:
  NEW     — соединение открыто: запоминаем цепочку и адресата;
  UPDATE  — приросты байт (раз в poll_interval секунд);
  CLOSED  — итог соединения: досчитываем остаток «итог − уже учтено».
Поэтому короткие соединения (закачка за доли секунды) учитываются полностью — опрос
/connections их не видел.

Снимок (`reset`) приходит при каждом подключении: итоги уже открытых соединений — только
точка отсчёта, без повторного счёта. Если соединение было известно до переподключения,
прирост за время разрыва досчитывается.

Нода — первый элемент цепочки (leaf), CRC из её тега; трафик тестера — цепочка, где есть
`nodes-tester`. Раз в flush_interval агрегаты пишутся в storage (traffic + endpoints).
Для монитора ведутся накопительные счётчики боевого трафика по CRC (user_bytes()).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from nodes_common.box_api import EVENT_CLOSED, EVENT_NEW, EVENT_UPDATE, connection_events, decode

from .api import ApiError
from .identity import parse_node

_BACKOFF_MAX = 30.0


def _host(addr: str) -> str:
    """'1.2.3.4:5' / '[::1]:5' / 'host' → хост без порта."""
    if addr.startswith("["):
        return addr[1:addr.find("]")] if "]" in addr else addr
    head, sep, tail = addr.rpartition(":")
    return head if sep and tail.isdigit() and ":" not in head else addr


class _Conn:
    __slots__ = ("crc", "is_tester", "endpoint", "up", "down")

    def __init__(self, crc, is_tester, endpoint, up=0, down=0):
        self.crc, self.is_tester, self.endpoint = crc, is_tester, endpoint
        self.up, self.down = up, down          # уже учтено


def _meta(conn: dict | None) -> tuple:
    conn = conn or {}
    chain = conn.get("chain") or []
    leaf = chain[0] if chain else conn.get("outbound", "")
    crc = parse_node(leaf).node_id or leaf or "?"
    is_tester = 1 if any("nodes-tester" in x for x in chain) else 0
    endpoint = (crc, _host(conn.get("source", "")),
                conn.get("domain") or _host(conn.get("destination", "")),
                conn.get("network", ""))
    return crc, is_tester, endpoint


class TrafficCollector:
    def __init__(self, cfg, api, storage=None):
        self.cfg = cfg                 # StorageConfig.traffic
        self.api = api
        self.storage = storage         # None — только счётчики для монитора, без записи
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._call = None
        self._conns: dict[str, _Conn] = {}
        # Буферы между сбросами (доступ из worker и из stop() → под _lock):
        self._by_node: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])  # crc,is_tester -> [up,down,conns]
        self._by_ep: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])    # crc,src,dst,net -> [up,down,flows]
        self._user: dict[str, int] = defaultdict(int)                      # crc -> байт (накопительно)
        self._lock = threading.Lock()
        self._connected = False

    def start(self) -> None:
        self._threads = [threading.Thread(target=self._loop, name="traffic", daemon=True)]
        if self.storage is not None:
            self._threads.append(threading.Thread(target=self._flush_loop,
                                                  name="traffic-flush", daemon=True))
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        call = self._call
        if call is not None:
            call.close()
        # Storage закрывается Runner сразу после stop(); дождаться worker нужно,
        # чтобы не потерять последнюю дельту и не писать в закрытую БД.
        for t in self._threads:
            t.join()
        self._threads = []
        if self.storage is not None:
            try:
                self._flush()   # дописать остаток
            except Exception as exc:  # noqa: BLE001 — дельты вернулись в буфер, не роняем stop
                print(f"  [traffic] финальный flush не удался: {exc}")

    def is_alive(self) -> bool:
        """Жив ли поток подписки (для /api/status админки)."""
        return bool(self._threads) and self._threads[0].is_alive()

    def connected(self) -> bool:
        """Подписка сейчас установлена (иначе учёт стоит до переподключения)."""
        return self._connected

    def user_bytes(self) -> dict[str, int]:
        """Накопительные байты боевого трафика (up+down) по CRC ноды."""
        with self._lock:
            return dict(self._user)

    # --- Внутреннее -----------------------------------------------------

    def _loop(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                self._call = self.api.subscribe_connections(self.cfg.poll_interval)
                for message in self._call:
                    if not self._connected:
                        self._connected, delay = True, 1.0
                    self.apply(connection_events(decode(message)))
            except ApiError as exc:
                if not self._stop.is_set():
                    print(f"  [traffic] подписка прервана: {exc}")
            except Exception as exc:  # noqa: BLE001 — сборщик не должен ронять процесс
                print(f"  [traffic] ошибка: {exc}")
            finally:
                self._connected = False
                self._call = None
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, _BACKOFF_MAX)

    def _flush_loop(self) -> None:
        while not self._stop.wait(self.cfg.flush_interval):
            try:
                self._flush()
            except Exception as exc:  # noqa: BLE001
                print(f"  [traffic] ошибка записи: {exc}")

    def _add(self, rec: _Conn, up: int, down: int, new: bool = False) -> None:
        up, down = max(0, up), max(0, down)
        if not (up or down or new):
            return
        nb = self._by_node[(rec.crc, rec.is_tester)]
        ep = self._by_ep[rec.endpoint]
        nb[0] += up; nb[1] += down
        ep[0] += up; ep[1] += down
        if new:
            nb[2] += 1
            ep[2] += 1
        if not rec.is_tester:
            self._user[rec.crc] += up + down

    def apply(self, batch: dict) -> None:
        """Применить одно сообщение ConnectionEvents (см. connection_events)."""
        with self._lock:
            if batch["reset"]:
                self._apply_snapshot(batch["events"])
                return
            for ev in batch["events"]:
                conn = ev["connection"]
                if ev["type"] == EVENT_NEW:
                    rec = _Conn(*_meta(conn))
                    self._conns[ev["id"]] = rec
                    self._add(rec, conn["uplink_total"] if conn else 0,
                              conn["downlink_total"] if conn else 0, new=True)
                    rec.up = conn["uplink_total"] if conn else 0
                    rec.down = conn["downlink_total"] if conn else 0
                elif ev["type"] == EVENT_UPDATE:
                    rec = self._conns.get(ev["id"])
                    if rec is None:                 # NEW пропущен — привязка по событию
                        rec = self._conns[ev["id"]] = _Conn(*_meta(conn))
                    self._add(rec, ev["uplink_delta"], ev["downlink_delta"])
                    rec.up += max(0, ev["uplink_delta"])
                    rec.down += max(0, ev["downlink_delta"])
                elif ev["type"] == EVENT_CLOSED:
                    rec = self._conns.pop(ev["id"], None)
                    if rec is None:
                        rec = _Conn(*_meta(conn))
                    if conn:
                        self._add(rec, conn["uplink_total"] - rec.up,
                                  conn["downlink_total"] - rec.down)

    def _apply_snapshot(self, events: list) -> None:
        known, self._conns = self._conns, {}
        for ev in events:
            conn = ev["connection"]
            if not conn:
                continue
            up, down = conn["uplink_total"], conn["downlink_total"]
            rec = known.get(ev["id"])
            if rec is not None:                     # было до разрыва — досчитать прирост
                self._add(rec, up - rec.up, down - rec.down)
            else:                                   # до нас — только точка отсчёта
                rec = _Conn(*_meta(conn))
            rec.up, rec.down = max(rec.up, up), max(rec.down, down)
            if not conn["closed_at"]:
                self._conns[ev["id"]] = rec

    def _flush(self) -> None:
        # Атомарно ЗАБИРАЕМ буферы (ставим пустые), запись — вне lock. При ошибке
        # записи возвращаем дельты обратно в буфер, чтобы не потерять (review.md P1).
        with self._lock:
            by_node, self._by_node = self._by_node, defaultdict(lambda: [0, 0, 0])
            by_ep, self._by_ep = self._by_ep, defaultdict(lambda: [0, 0, 0])
        if not by_node and not by_ep:
            return
        ts = int(time.time())
        node_rows = [(crc, v[0], v[1], v[2], is_tester)
                     for (crc, is_tester), v in by_node.items()]
        ep_rows = [(crc, src, dst, net, v[0], v[1], v[2])
                   for (crc, src, dst, net), v in by_ep.items()]
        try:
            self.storage.add_traffic_batch(ts, node_rows, ep_rows)   # одна транзакция
        except Exception:  # noqa: BLE001 — не теряем дельты: возвращаем в буфер
            with self._lock:
                for k, v in by_node.items():
                    b = self._by_node[k]; b[0] += v[0]; b[1] += v[1]; b[2] += v[2]
                for k, v in by_ep.items():
                    b = self._by_ep[k]; b[0] += v[0]; b[1] += v[1]; b[2] += v[2]
            raise
