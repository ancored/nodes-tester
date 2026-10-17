"""VACUUM базы тестера по расписанию storage.vacuum и по кнопке в админке.

VACUUM идёт в отдельном потоке на соединении тестера: пока он работает, запись в базу ждёт
блокировки хранилища (замеры и монитор не теряются, только задерживаются). Итог последнего
запуска — в meta['vacuum']: время, причина, размер базы до и после, длительность или ошибка.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time

_KEY = "vacuum"
_RETRY = 86400.0                      # после ошибки — повтор через сутки, а не через every


def next_due(last: float, every: float, at: str) -> float:
    """Первое время at (ЧЧ:ММ, местное) не раньше last + every."""
    base = dt.datetime.fromtimestamp(last + every)
    hour, minute = map(int, at.split(":"))
    slot = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot < base:
        slot += dt.timedelta(days=1)
    return slot.timestamp()


class Vacuum:
    def __init__(self, storage, cfg, clock=time.time):
        self.storage = storage
        self.cfg = cfg                         # VacuumConfig: every (с), at (ЧЧ:ММ)
        self.clock = clock
        self._lock = threading.Lock()
        self._thread = None
        if self.last() is None:
            # Отсчёт расписания — от первого запуска тестера, а не «сразу»: VACUUM тяжёлый.
            self._save({"ts": clock()})

    def last(self) -> "dict | None":
        try:
            return json.loads(self.storage.get_meta(_KEY) or "null")
        except ValueError:
            return None

    def _save(self, record: dict) -> None:
        self.storage.set_meta(_KEY, json.dumps(record))

    def next(self) -> "float | None":
        """Следующий запуск по расписанию; every 0 — только вручную."""
        if self.cfg.every <= 0:
            return None
        last = self.last() or {}
        return next_due(last.get("ts", self.clock()), _RETRY if last.get("error") else self.cfg.every,
                        self.cfg.at)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def due(self, now: float) -> bool:
        when = self.next()
        return when is not None and now >= when and not self.running()

    def start(self, trigger: str) -> bool:
        """Запустить VACUUM в фоне; False — он уже идёт."""
        with self._lock:
            if self.running():
                return False
            self._thread = threading.Thread(target=self._run, args=(trigger,), name="vacuum", daemon=True)
            self._thread.start()
            return True

    def _run(self, trigger: str) -> None:
        started = self.clock()
        record = {"ts": started, "trigger": trigger, "size_before": self.storage.size()}
        print(f"[storage] VACUUM ({trigger}): база {record['size_before'] / 1e6:.1f} МБ…")
        try:
            self.storage.vacuum()
            record["size_after"] = self.storage.size()
            print(f"[storage] VACUUM выполнен: {record['size_after'] / 1e6:.1f} МБ")
        except sqlite3.Error as exc:
            record["error"] = str(exc)
            print(f"[storage] VACUUM не выполнен: {exc}")
        record["duration"] = round(self.clock() - started, 1)
        self._save(record)

    def state(self) -> dict:
        """Для админки: идёт ли, итог последнего запуска, следующий срок и размер базы сейчас."""
        last = self.last() or {}
        return {"running": self.running(), "last": last if "size_before" in last else None,
                "next": self.next(), "size": self.storage.size(),
                "every": self.cfg.every, "at": self.cfg.at}
