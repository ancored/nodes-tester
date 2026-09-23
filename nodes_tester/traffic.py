"""Сборщик трафика по нодам поверх Clash API /connections → SQLite.

Фоновый поток: поллит /connections, ведёт ДЕЛЬТА-учёт по id соединения
(в /connections байты кумулятивные), привязывает к ноде по CRC из chains[0],
отделяет собственный трафик тестера (chain содержит nodes-tester) и раз в
flush_interval пишет агрегаты в storage (traffic + endpoints).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from .clash_api import ClashApiError
from .identity import parse_node


class TrafficCollector:
    def __init__(self, cfg, clash, storage):
        self.cfg = cfg                 # StorageConfig.traffic
        self.clash = clash
        self.storage = storage
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._committed: dict[str, tuple[int, int]] = {}   # conn_id -> (up, down)
        # Буферы между сбросами (доступ из worker и из stop() → под _lock):
        self._by_node: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])  # crc,is_tester -> [up,down,conns]
        self._by_ep: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])    # crc,src,dst,net -> [up,down,flows]
        self._lock = threading.Lock()
        self._last_flush = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="traffic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Storage закрывается Runner сразу после stop(); дождаться worker нужно,
            # чтобы не потерять последнюю дельту и не писать в закрытую БД.
            self._thread.join()
            self._thread = None
        try:
            self._flush()   # дописать остаток
        except Exception as exc:  # noqa: BLE001 — дельты вернулись в буфер, не роняем stop
            print(f"  [traffic] финальный flush не удался: {exc}")

    def is_alive(self) -> bool:
        """Жив ли фоновый поток сборщика (для /api/status админки)."""
        return self._thread is not None and self._thread.is_alive()

    # --- Внутреннее -----------------------------------------------------

    def _loop(self) -> None:
        self._last_flush = time.monotonic()
        while not self._stop.wait(self.cfg.poll_interval):
            try:
                self._poll()
                if time.monotonic() - self._last_flush >= self.cfg.flush_interval:
                    self._flush()
            except Exception as exc:  # noqa: BLE001 — сборщик не должен ронять процесс
                print(f"  [traffic] ошибка: {exc}")

    def _poll(self) -> None:
        try:
            conns = self.clash.connections()
        except ClashApiError:
            return  # API недоступен — пропускаем

        seen = set()
        for c in conns:
            cid = c.get("id")
            if not cid:
                continue
            seen.add(cid)
            up, down = int(c.get("upload", 0)), int(c.get("download", 0))
            pup, pdown = self._committed.get(cid, (0, 0))
            dup, ddown = up - pup, down - pdown
            self._committed[cid] = (up, down)
            if dup <= 0 and ddown <= 0:
                continue

            chains = c.get("chains") or []
            leaf = chains[0] if chains else ""
            crc = parse_node(leaf).node_id or leaf or "?"
            is_tester = 1 if any("nodes-tester" in x for x in chains) else 0
            meta = c.get("metadata") or {}

            with self._lock:
                nb = self._by_node[(crc, is_tester)]
                nb[0] += max(0, dup); nb[1] += max(0, ddown)
                if pup == 0 and pdown == 0:      # первое появление соединения
                    nb[2] += 1
                ep = self._by_ep[(crc, meta.get("sourceIP", ""),
                                  meta.get("host") or meta.get("destinationIP", ""),
                                  meta.get("network", ""))]
                ep[0] += max(0, dup); ep[1] += max(0, ddown)
                if pup == 0 and pdown == 0:
                    ep[2] += 1

        # Забываем закрытые соединения.
        for cid in list(self._committed):
            if cid not in seen:
                del self._committed[cid]

    def _flush(self) -> None:
        self._last_flush = time.monotonic()
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
