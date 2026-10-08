"""Уведомления владельцу: Telegram и/или webhook.

События (`notify.events`):
  switch        — переключение ноды (причины — `notify.switch_reasons`);
  failsafe      — группа ушла на {group}-auto-out-failsafe (годных нод нет);
  rollback      — новый конфиг sing-box не дал связности, вернули прежний (или не помогло);
  pipeline      — прогон конвейера завершился ошибкой/тайм-аутом;
  subscription  — подписка не загрузилась (работают прошлые ноды — до какого времени — или нод нет);
  expiry        — подписка заканчивается (по Subscription-Userinfo) — раз в сутки;
  singbox       — sing-box остановлен/запущен из админки, переключён killswitch.

Отправка — фоновым потоком из очереди: тестер и переключатель не ждут сеть. Повтор того
же ключа (`key`) в пределах `dedupe_minutes` не отправляется; общий предел — `max_per_hour`.
Токен бота в лог не попадает.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections import deque

import requests

EVENTS = ("switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox")
SWITCH_REASONS = ("init", "rotation", "quality", "emergency", "emergency-stuck", "manual")

_REASON_RU = {
    "init": "первый выбор", "rotation": "ротация", "quality": "лучше по качеству",
    "emergency": "авария активной ноды", "emergency-stuck": "авария, замены нет",
    "manual": "вручную", "failsafe": "запасная группа",
}
_SECRET_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def _mask(text: str) -> str:
    return _SECRET_RE.sub("bot***", str(text))


def _epoch(iso) -> float:
    """'2026-09-30T00:00:03Z' → unix-время (0 — не распознано)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class Notifier:
    def __init__(self, cfg, session_factory=requests.Session, clock=time.time, fallback=None):
        self.cfg = cfg
        self.clock = clock
        # fallback(fn) → fn(session) через прокси тестера; для каналов, которые напрямую
        # с роутера недоступны (Telegram режется провайдером).
        self._fallback = fallback
        self._session_factory = session_factory
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._sent: dict[str, float] = {}           # key → время последней отправки
        self._recent: deque = deque()               # времена отправок за последний час
        self._lock = threading.Lock()
        self._thread = None
        self.last_error = None

    # --- Настройки ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and (self._tg() or self.cfg.webhook.url))

    def _tg(self) -> bool:
        return bool(self.cfg.telegram.token and self.cfg.telegram.chat_id)

    def wants(self, event: str) -> bool:
        return self.enabled and event in self.cfg.events

    # --- Постановка в очередь ------------------------------------------

    def send(self, event: str, text: str, key: "str | None" = None, force: bool = False,
             ttl: "float | None" = None) -> bool:
        """Поставить сообщение в очередь. False — отключено/отфильтровано/дубль/лимит.
        ttl — окно подавления повтора ключа, с (по умолчанию dedupe_minutes)."""
        if not force and not self.wants(event):
            return False
        if force and not self.enabled:
            return False
        now = self.clock()
        with self._lock:
            window = self.cfg.dedupe_minutes * 60 if ttl is None else ttl
            if key and not force:
                last = self._sent.get(key)
                if last is not None and now - last < window:
                    return False
            if len(self._sent) > 500:                      # старые ключи не копим
                self._sent = {k: t for k, t in self._sent.items() if now - t < 86400}
            while self._recent and now - self._recent[0] > 3600:
                self._recent.popleft()
            if not force and len(self._recent) >= self.cfg.max_per_hour:
                return False
            if key:
                self._sent[key] = now
            self._recent.append(now)
        name = (self.cfg.name or "").strip()
        message = f"[{name}] {text}" if name else text
        try:
            self._queue.put_nowait((event, message))
        except queue.Full:
            return False
        self._ensure_worker()
        return True

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="notify", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        session = self._session_factory()
        while True:
            try:
                event, message = self._queue.get(timeout=60)
            except queue.Empty:
                return                                      # поток поднимется при новом событии
            self.deliver(session, event, message)

    def deliver(self, session, event: str, message: str) -> bool:
        """Отправить сразу (из потока очереди или для тестового сообщения). Канал, не
        прошедший напрямую (или через notify.proxy), повторяется через прокси тестера."""
        ok = True
        if self._tg():
            ok &= self._via("Telegram", session, lambda s: self._post_telegram(s, message))
        if self.cfg.webhook.url:
            ok &= self._via("webhook", session, lambda s: self._post_webhook(s, event, message))
        return ok

    def _via(self, channel: str, session, send) -> bool:
        try:
            send(session)
            return True
        except Exception as exc:  # noqa: BLE001 — уведомление не должно ронять тестер
            first = exc
        if self._fallback is not None:
            def attempt(s):
                send(s)
                return True
            try:
                if self._fallback(attempt):
                    return True
            except Exception as exc:  # noqa: BLE001
                first = exc
        self._fail(channel, first)
        return False

    def _proxies(self):
        return {"http": self.cfg.proxy, "https": self.cfg.proxy} if self.cfg.proxy else None

    def _post_telegram(self, session, message: str) -> None:
        r = session.post(f"https://api.telegram.org/bot{self.cfg.telegram.token}/sendMessage",
                         json={"chat_id": self.cfg.telegram.chat_id, "text": message,
                               "disable_web_page_preview": True},
                         timeout=self.cfg.timeout, proxies=self._proxies())
        if r.status_code != 200:
            raise RuntimeError(f"Telegram HTTP {r.status_code}: {r.text[:200]}")

    def _post_webhook(self, session, event: str, message: str) -> None:
        r = session.post(self.cfg.webhook.url,
                         json={"event": event, "text": message, "ts": int(self.clock())},
                         timeout=self.cfg.timeout, proxies=self._proxies())
        if r.status_code >= 300:
            raise RuntimeError(f"webhook HTTP {r.status_code}")

    def _fail(self, channel: str, exc: Exception) -> None:
        self.last_error = _mask(f"{channel}: {type(exc).__name__}: {exc}")
        print(f"  [notify] не отправлено — {self.last_error}")

    def test(self) -> dict:
        """Тестовое сообщение синхронно (для кнопки в админке)."""
        if not self.enabled:
            return {"ok": False, "error": "уведомления выключены или не заданы получатели"}
        self.last_error = None
        name = (self.cfg.name or "").strip()
        ok = self.deliver(self._session_factory(), "test",
                          (f"[{name}] " if name else "") + "nodes-tester: тестовое уведомление")
        return {"ok": ok, "error": None if ok else self.last_error}

    # --- События --------------------------------------------------------

    def activation(self, group: str, reason: str, node: str, prev: "str | None") -> None:
        """Из переключателя: каждая запись истории переключений."""
        if reason == "failsafe":
            self.send("failsafe", f"Группа {group}: годных нод нет — переход на запасную "
                                  f"группу {group}-auto-out-failsafe.", key=f"failsafe:{group}")
            return
        if reason not in self.cfg.switch_reasons:
            return
        why = _REASON_RU.get(reason, reason)
        if reason == "emergency-stuck":
            text = f"Группа {group}: активная нода не работает, замены нет ({node or '—'})."
        else:
            text = f"Группа {group}: {why} → {node}" + (f" (была {prev})" if prev else "")
        self.send("switch", text, key=None if reason != "emergency-stuck" else f"stuck:{group}")

    def pipeline_finished(self, row: dict, log_text: str) -> None:
        """Из оркестратора: разбор журнала прогона."""
        mode = row.get("mode") or "?"
        if row.get("dry_run"):
            return
        for line in log_text.splitlines():
            if "откатились на прежний config.json" in line:
                self.send("rollback", "Новый конфиг sing-box не дал связности — вернули "
                                      "прежний, связность есть.", key="rollback")
            elif "связности нет и на прежнем конфиге" in line:
                self.send("rollback", "ВНИМАНИЕ: связности нет ни на новом, ни на прежнем "
                                      "конфиге sing-box — нужна ручная проверка.", key="rollback-fail")
        status = row.get("status")
        if status in ("error", "timeout"):
            label = {"router": "роутер", "clients": "клиенты", "apply": "применение правил",
                     "apply-clients": "сборка клиентов"}
            self.send("pipeline", f"Конвейер «{label.get(mode, mode)}» завершился: "
                                  f"{'тайм-аут' if status == 'timeout' else 'ошибка'}. "
                                  f"Журнал — в админке, «Конвейер».", key=f"pipeline:{mode}")

    def subscriptions_state(self, sources: list, stale_max_hours: float = 48.0) -> None:
        """По raw после прогона: сбой загрузки (и когда пропадут прошлые ноды), срок и
        трафик подписки (Subscription-Userinfo)."""
        now = self.clock()
        day = 86400
        for src in sources or []:
            tag = src.get("provider") or "?"
            if src.get("ok") is False:
                err = _mask(src.get("error") or "ошибка")[:200]
                if src.get("stale"):
                    left = ""
                    last = _epoch(src.get("last_ok_at"))
                    if last:
                        hours = (last + stale_max_hours * 3600 - now) / 3600
                        left = f", пропадут через ~{max(0, int(hours))} ч, если загрузка не восстановится"
                    note = f"работают прошлые ноды ({src.get('count', 0)}){left}"
                else:
                    note = "нод от неё нет"
                self.send("subscription", f"Подписка {tag} не загрузилась ({err}) — {note}.",
                          key=f"sub:{tag}")
            meta = src.get("meta") or {}
            expire = int(meta.get("expire") or 0)
            if expire > 0:
                days = (expire - now) / 86400
                if days <= self.cfg.expiry_days:
                    when = time.strftime("%d.%m", time.localtime(expire))
                    text = (f"Подписка {tag} закончилась {when}." if days <= 0 else
                            f"Подписка {tag} заканчивается {when} (осталось {max(1, int(days + 0.999))} дн.).")
                    self.send("expiry", text, key=f"expiry:{tag}", ttl=day)
            total, used = int(meta.get("total") or 0), int(meta.get("upload") or 0) + int(meta.get("download") or 0)
            if total > 0 and used >= total * 0.9:
                self.send("expiry", f"Подписка {tag}: израсходовано {used * 100 // total}% трафика.",
                          key=f"traffic:{tag}", ttl=day)
