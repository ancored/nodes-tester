"""Общая авария — отказ не ноды, а общего звена: WAN роутера или слой нод целиком (DNS
нод, блокировка). Пока она идёт, проверки, резерв, ротации и аварийные замены остановлены
(Runner._paused): иначе тестер перебирал бы ноды по кругу и наказывал их паузами и штрафами
за чужую поломку.

Прямой выход проверяется в фоне раз в monitor.outage.interval: рукопожатие TCP (для имени —
и TLS с проверкой сертификата) с targets мимо sing-box — с его routing_mark и через WAN.
Не ответили fail_quorum целей — авария WAN. Сигнал от переключателя — switches аварийных
замен за switches_window — запускает проверку сразу: прямой выход и до `nodes` нод разных
поставщиков через тестовый селектор. Прямой выход жив, а ноды все молчат — авария нод.
Конец — recover удачных проверок подряд (раз в recheck).
"""

from __future__ import annotations

import collections
import json
import socket
import ssl
import threading
import time

_SO_MARK = 36                     # нет в модуле socket на OpenWrt
_SO_BINDTODEVICE = 25
_RESOLVE_EVERY = 3600.0           # адреса целей по имени освежать не чаще


def _setopt(sock: socket.socket, option: int, value) -> None:
    """Опция сокета уровня SOL_SOCKET; не Linux или нет прав — проверка идёт без неё."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, option, value)
    except OSError:
        pass


def _wan_interface(singbox_config: str) -> str:
    """route.default_interface из конфига sing-box; пусто — не задан."""
    try:
        with open(singbox_config, encoding="utf-8") as fh:
            return str((json.load(fh).get("route") or {}).get("default_interface") or "")
    except (OSError, ValueError):
        return ""


class OutageGuard:
    def __init__(self, cfg, singbox_config: str, node_alive, node_pool, notifier=None, busy=None,
                 connect=None):
        self.cfg = cfg                       # OutageConfig
        self.interface = cfg.interface or _wan_interface(singbox_config)
        # node_alive(leaf) -> bool — нода отвечает (зонд через тестовый селектор);
        # node_pool() -> [leaf] — кандидаты разных поставщиков, лучшие первыми.
        self.node_alive = node_alive
        self.node_pool = node_pool
        self.notifier = notifier
        self.busy = busy or (lambda: False)  # конвейер применяет конфиг — не проверять
        self.connect = connect or self._handshake
        self.active: dict | None = None      # {"kind": "wan"|"nodes", "since", "detail"}
        self._switches: collections.deque = collections.deque()
        self._suspect = False
        self._good = 0
        self._last_direct = 0.0
        self._ips: dict[str, tuple[str, float]] = {}   # имя цели → (адрес, когда получен)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.cfg.enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="outage", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def note_switch(self, reason: str) -> None:
        """Из переключателя: аварийная замена. switches за switches_window — проверка сразу."""
        if not self.cfg.enabled or reason != "emergency" or self.active:
            return
        now = time.monotonic()
        self._switches.append(now)
        while self._switches and self._switches[0] < now - self.cfg.switches_window:
            self._switches.popleft()
        if len(self._switches) >= self.cfg.switches:
            self._switches.clear()
            self._suspect = True
            self._wake.set()

    # --- Цикл -------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.cfg.recheck)    # срок фоновой проверки отсчитывает step()
            self._wake.clear()
            if self._stop.is_set():
                return
            if self.busy():
                continue
            try:
                self.step(time.monotonic())
            except Exception as exc:  # noqa: BLE001 — сторож не роняет процесс
                print(f"  [outage] ошибка: {exc}")

    def step(self, now: float) -> None:
        """Один шаг: в аварии — проверка её конца; иначе по сигналу — прямой выход и ноды,
        по сроку — только прямой выход."""
        if self.active:
            self._check_recovery(now)
            return
        suspect, self._suspect = self._suspect, False
        due = self.cfg.interval and now - self._last_direct >= self.cfg.interval
        if not (suspect or due):
            return
        self._last_direct = now
        wan_ok, detail = self.direct()
        if not wan_ok:
            self._enter("wan", detail, now)
        elif suspect:
            nodes_ok, detail = self.nodes()
            if not nodes_ok:
                self._enter("nodes", detail, now)

    def _check_recovery(self, now: float) -> None:
        kind = self.active["kind"]
        ok, detail = self.direct() if kind == "wan" else self.nodes()
        self._good = self._good + 1 if ok else 0
        if self._good < self.cfg.recover:
            return
        minutes = (now - self.active["since"]) / 60
        print(f"  [outage] общая авария закончилась ({kind}, {minutes:.0f} мин): проверки и замены возобновлены")
        if self.notifier is not None:
            self.notifier.outage(kind, detail, minutes)
        self.active, self._good = None, 0
        self._switches.clear()

    def _enter(self, kind: str, detail: str, now: float) -> None:
        self.active, self._good = {"kind": kind, "since": now, "detail": detail}, 0
        what = "прямой выход не отвечает" if kind == "wan" else "ноды разных поставщиков не отвечают"
        print(f"  [outage] ОБЩАЯ АВАРИЯ: {what} ({detail}) — проверки и замены нод остановлены")
        if kind == "nodes" and self.notifier is not None:
            self.notifier.outage(kind, detail)

    # --- Проверки ---------------------------------------------------------

    def direct(self) -> tuple[bool, str]:
        """Прямой выход: не ответили меньше fail_quorum целей — жив."""
        failed = [t for t in self.cfg.targets if not self.connect(t)]
        ok = len(failed) < self.cfg.fail_quorum
        return ok, (f"не ответили: {', '.join(failed)}" if failed
                    else f"ответили все {len(self.cfg.targets)}")

    def nodes(self) -> tuple[bool, str]:
        """Слой нод: жив, если ответила хоть одна из нод разных поставщиков (или проверять
        некого)."""
        pool = self.node_pool()[: self.cfg.nodes]
        if not pool:
            return True, "проверять некого"
        for leaf in pool:
            if self.node_alive(leaf):
                return True, f"ответила {leaf}"
        return False, f"не ответили {len(pool)} нод разных поставщиков"

    def _handshake(self, target: str) -> bool:
        """TCP (и TLS для имени) с целью мимо sing-box: его routing_mark, интерфейс WAN."""
        host, port = target.rsplit(":", 1)
        address = self._address(host)
        if not address:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if self.cfg.mark:
                _setopt(sock, _SO_MARK, self.cfg.mark)
            if self.interface:
                _setopt(sock, _SO_BINDTODEVICE, self.interface.encode())
            sock.settimeout(self.cfg.timeout)
            sock.connect((address, int(port)))
            if address != host:                  # имя — проверить и TLS: заглушка провайдера не пройдёт
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
                    pass
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _address(self, host: str) -> str:
        """IPv4 цели. Имя резолвится не чаще раза в час; не резолвится — прежний адрес."""
        try:
            socket.inet_aton(host)
            return host
        except OSError:
            pass
        cached = self._ips.get(host)
        if cached and time.monotonic() - cached[1] < _RESOLVE_EVERY:
            return cached[0]
        try:
            address = socket.gethostbyname(host)
        except OSError:
            return cached[0] if cached else ""
        self._ips[host] = (address, time.monotonic())
        return address
