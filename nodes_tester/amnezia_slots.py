"""Страны Amnezia Premium по рейтингу (подписки type=amnezia ветви router).

Раз в CHECK_EVERY тестер сверяет файлы стран в каталоге подписки (`<код страны>.conf`) с
рейтингом их нод (нода находится по адресу сервера из файла):

- страна, чья нода держит рейтинг ниже min_rating дольше HOLD, считается заблокированной: её
  файл отзывается на шлюзе и удаляется, страна запоминается и больше не выпускается;
- свободные места (не больше slots и свободных слотов подписки) занимают случайные страны:
  не выпущенные ранее этой подпиской, не заблокированные и не из exclude_countries;
- файл, чей сервер обновился после выдачи (worker_last_updated новее last_downloaded),
  выпускается заново — тот же слот, новый ключ.

Если ниже порога все страны с рейтингом (а их больше одной), дело не в странах (режут сам протокол или упала сеть):
замены пропускаются. Страны, выпущенные не тестером (файла в каталоге нет), не трогаются.
Новые файлы попадают в nodes.json со следующим опросом подписок и сборкой роутера.
Память — state.json в каталоге подписки: blocked и issued (код → дата), low_since (код → с какого
времени рейтинг ниже порога).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timezone

from nodes_common.fileio import atomic_write_private, dumps_json
from nodes_fetch.amnezia import AmneziaError, Gateway
from nodes_fetch.parsers import awg

CHECK_EVERY = 3 * 3600.0
FIRST_CHECK = 600.0                     # после старта тестера: рейтинг успевает загрузиться
HOLD = 6 * 3600.0
MIN_RATING = 40.0
EXCLUDE = ("ru",)
STATE = "state.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve(path: str, base_dir: str) -> str:
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


class Subscription:
    """Одна подписка type=amnezia: каталог файлов, память и шлюз."""

    def __init__(self, sub: dict, base_dir: str, proxy: str = "", log=print, clock=time.time):
        self.tag = sub["tag"]
        self.sub = sub
        self.folder = _resolve(sub["path"], base_dir)
        self.key_file = _resolve(sub["gateway_key"], base_dir)
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.log = log
        self.clock = clock

    def _say(self, msg: str) -> None:
        self.log(f"[amnezia] {self.tag}: {msg}")

    def _load_state(self) -> dict:
        try:
            with open(os.path.join(self.folder, STATE), encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        return {k: dict(state.get(k) or {}) for k in ("blocked", "issued", "low_since")}

    def _files(self) -> dict[str, str]:
        """Код страны → адрес сервера из её файла."""
        out = {}
        for name in sorted(os.listdir(self.folder)):
            if name.endswith(".conf"):
                try:
                    out[name[:-5]] = awg.parse_file(os.path.join(self.folder, name))["peers"][0]["address"].lower()
                except (OSError, ValueError, KeyError):
                    self._say(f"{name} не разбирается — пропущен")
        return out

    def _low(self, files: dict, ratings: dict, state: dict, now: float) -> list[str]:
        """Страны ниже порога дольше HOLD; обновляет low_since. Все оценённые ниже — пусто."""
        threshold = float(self.sub.get("min_rating", MIN_RATING))
        rated = {cc: ratings[server] for cc, server in files.items() if server in ratings}
        low_since = state["low_since"]
        for cc in list(low_since):
            if cc not in rated or rated[cc] >= threshold:
                del low_since[cc]
        for cc, score in rated.items():
            if score < threshold:
                low_since.setdefault(cc, now)
        if len(rated) > 1 and len(low_since) == len(rated):
            self._say(f"все страны ниже {threshold:g} — похоже, дело не в странах; замены пропущены")
            return []
        return [cc for cc, since in low_since.items() if now - since >= HOLD]

    def _write(self, cc: str, conf: str, state: dict) -> None:
        atomic_write_private(os.path.join(self.folder, f"{cc}.conf"), conf)
        state["issued"][cc] = _today()

    def run(self, ratings: dict) -> list[str]:
        """Одна сверка. ratings — {адрес сервера: рейтинг}. Возвращает сделанное (для уведомления)."""
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        state, now, done = self._load_state(), self.clock(), []
        files = self._files()
        low = self._low(files, ratings, state, now)
        try:
            with open(self.key_file, encoding="utf-8") as fh:
                gateway = Gateway(self.sub["url"], fh.read(), proxies=self.proxies)
            info = gateway.account_info()
            issued = {c.get("server_country_code"): c for c in info.get("issued_configs") or []
                      if c.get("source_type") == "country_config"}
            for cc in low:
                gateway.revoke(cc)
                os.unlink(os.path.join(self.folder, f"{cc}.conf"))
                files.pop(cc)
                state["blocked"][cc] = _today()
                done.append(f"{cc} заблокирована (рейтинг ниже порога) — файл отозван")
            for cc in files:
                c = issued.get(cc) or {}
                if c.get("worker_last_updated", "") > c.get("last_downloaded", "~"):
                    self._write(cc, gateway.issue(cc), state)
                    done.append(f"{cc} перевыпущена: сервер обновился")
            limit = int(self.sub.get("slots") or info.get("max_device_count") or 0)
            free = int(info.get("max_device_count") or 0) - int(info.get("active_device_count") or 0) + len(low)
            exclude = set(self.sub.get("exclude_countries", EXCLUDE)) | set(state["blocked"]) | set(issued) | set(files)
            pool = [c.get("server_country_code") for c in info.get("available_countries") or []
                    if "awg" in (c.get("available_protocols") or []) and c.get("server_country_code") not in exclude]
            random.shuffle(pool)
            for cc in pool[:max(0, min(limit - len(files), free))]:
                self._write(cc, gateway.issue(cc), state)
                done.append(f"{cc} выпущена")
        except (AmneziaError, OSError) as exc:
            self._say(f"ошибка: {exc}")
        finally:
            atomic_write_private(os.path.join(self.folder, STATE), dumps_json(state))
        for line in done:
            self._say(line)
        return done


class AmneziaSlots:
    """Расписание сверок в тестере: фоновый поток раз в CHECK_EVERY."""

    def __init__(self, cfg, ratings, notifier=None, log=print, clock=time.time):
        from .config import branch_subscriptions
        base_dir = os.path.dirname(cfg.path)
        self.subs = [Subscription(s, base_dir, cfg.pipeline.fetch.proxy, log, clock)
                     for s in branch_subscriptions(cfg.subscriptions, "router")
                     if s.get("type") == "amnezia" and s.get("enabled", True) is not False]
        self.ratings = ratings              # () -> {адрес сервера: рейтинг}
        self.notifier = notifier
        self.clock = clock
        self._next = clock() + FIRST_CHECK
        self._thread = None

    def due(self, now: float) -> bool:
        return bool(self.subs) and now >= self._next and not (self._thread and self._thread.is_alive())

    def start(self) -> None:
        self._next = self.clock() + CHECK_EVERY
        self._thread = threading.Thread(target=self._run, name="amnezia", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        ratings = self.ratings()
        for sub in self.subs:
            done = sub.run(ratings)
            if done and self.notifier:
                self.notifier.send("subscription", f"Amnezia {sub.tag}: " + "; ".join(done) + ".",
                                   key=f"amnezia-{sub.tag}")
