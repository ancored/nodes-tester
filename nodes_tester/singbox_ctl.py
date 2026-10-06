"""Остановка/запуск sing-box из админки и управление службой killswitch (OpenWrt).

Остановка: `/etc/init.d/sing-box stop`. sing-box в режиме TUN (auto_route, auto_redirect)
при остановке сам снимает маршруты и правила nftables. Пока стоит флаг остановки
(`stop_flag`, tmpfs — сбрасывается перезагрузкой), apply-nodes.sh не применяет новый конфиг
и не поднимает sing-box.

Killswitch — отдельная служба роутера (`killswitch_init`, по умолчанию /etc/init.d/killswitch):
постоянная таблица nftables (`killswitch_table`), которая стартует до сети и отбивает
пересылку LAN → WAN мимо sing-box. nodes-tester свои правила не заводит: показывает,
загружена ли таблица и сколько пакетов отбито, и включает/выключает службу (start + enable,
stop + disable).

Сторожевой поток сообщает (notify, событие singbox), если sing-box не работает без команды
из админки дольше `down_alert` секунд, и когда он поднялся.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

_COUNTER_RE = re.compile(r"counter packets (\d+)")
_KS_UNKNOWN = {"active": False, "enabled": False, "blocked": None}


class ControlError(RuntimeError):
    pass


class SingboxControl:
    def __init__(self, cfg, notifier=None, run=subprocess.run, clock=time.monotonic):
        self.cfg = cfg                      # SingboxControlConfig
        self.notifier = notifier
        self._run = run
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._down_since = None
        self._down_alerted = False
        self.last_error = None

    # --- Окружение ------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self.cfg.enabled and os.path.exists(self.cfg.init))

    def _cmd(self, args, input_text=None, timeout=60) -> subprocess.CompletedProcess:
        return self._run(args, input=input_text, capture_output=True, text=True, timeout=timeout)

    def running(self) -> bool:
        try:
            return self._cmd(["pidof", "sing-box"], timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def stopped_by_admin(self) -> bool:
        return os.path.exists(self.cfg.stop_flag)

    # --- Killswitch (служба роутера) -----------------------------------

    def killswitch_installed(self) -> bool:
        return os.path.exists(self.cfg.killswitch_init)

    def killswitch_status(self) -> dict:
        """{installed, active (таблица загружена), enabled (автозапуск), blocked (пакетов)}."""
        if not self.killswitch_installed():
            return {"installed": False, **_KS_UNKNOWN}
        try:
            res = self._cmd(["nft", "list", "table", "inet", self.cfg.killswitch_table], timeout=10)
            active = res.returncode == 0
            blocked = sum(int(n) for n in _COUNTER_RE.findall(res.stdout or "")) if active else None
            enabled = self._cmd([self.cfg.killswitch_init, "enabled"], timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return {"installed": True, **_KS_UNKNOWN}
        return {"installed": True, "active": active, "enabled": enabled, "blocked": blocked}

    def status(self) -> dict:
        avail = self.available
        return {
            "available": avail,
            "running": self.running() if avail else None,
            "stopped_by_admin": self.stopped_by_admin() if avail else False,
            "killswitch": self.killswitch_status() if avail else {"installed": False, **_KS_UNKNOWN},
            "error": self.last_error,
        }

    # --- Действия -------------------------------------------------------

    def _require(self) -> None:
        if not self.available:
            raise ControlError("управление sing-box доступно только на OpenWrt "
                               f"(нет {self.cfg.init} или singbox_control.enabled=false)")

    def stop(self) -> dict:
        self._require()
        with self._lock:
            os.makedirs(os.path.dirname(self.cfg.stop_flag), exist_ok=True)
            with open(self.cfg.stop_flag, "w", encoding="utf-8") as fh:
                fh.write(str(int(time.time())))
            res = self._cmd([self.cfg.init, "stop"])
            if res.returncode != 0:
                raise ControlError(f"sing-box stop: {res.stderr.strip() or res.returncode}")
            self._wait_running(False)
        ks = self.killswitch_status()
        self._notify("sing-box остановлен из админки — " + (
            "killswitch блокирует выход LAN мимо sing-box." if ks["active"]
            else "killswitch не загружен, трафик LAN идёт напрямую через провайдера."), "singbox-stop")
        return self.status()

    def start(self) -> dict:
        self._require()
        with self._lock:
            try:
                os.remove(self.cfg.stop_flag)
            except FileNotFoundError:
                pass
            res = self._cmd([self.cfg.init, "start"])
            if res.returncode != 0:
                raise ControlError(f"sing-box start: {res.stderr.strip() or res.returncode}")
            self._wait_running(True)
        self._notify("sing-box запущен из админки.", "singbox-start")
        return self.status()

    def _wait_running(self, want: bool) -> None:
        """До 10 с ждать, пока процесс sing-box появится (want) или завершится."""
        for _ in range(20):
            if self.running() == want:
                return
            time.sleep(0.5)

    def set_killswitch(self, on: bool) -> dict:
        """Включить (start + enable) или выключить (stop + disable) службу killswitch."""
        self._require()
        if not self.killswitch_installed():
            raise ControlError(f"служба killswitch не установлена ({self.cfg.killswitch_init})")
        with self._lock:
            for action in (("start", "enable") if on else ("stop", "disable")):
                res = self._cmd([self.cfg.killswitch_init, action])
                if res.returncode != 0:
                    raise ControlError(f"killswitch {action}: {res.stderr.strip() or res.returncode}")
        self._notify(f"Killswitch {'включён' if on else 'выключен'} из админки"
                     + ("." if on else ": при остановке sing-box трафик LAN пойдёт напрямую."),
                     "killswitch")
        return self.status()

    # --- Сторож ---------------------------------------------------------

    def start_watch(self) -> None:
        if not self.available or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._watch, name="singbox-ctl", daemon=True)
        self._thread.start()

    def stop_watch(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        while not self._stop.wait(self.cfg.interval):
            try:
                self._check_down()
            except Exception as exc:  # noqa: BLE001 — сторож не должен падать
                print(f"  [singbox] сторож: {type(exc).__name__}: {exc}")

    def _check_down(self) -> None:
        now = self._clock()
        if self.running() or self.stopped_by_admin():
            if self._down_alerted:
                self._notify("sing-box снова работает.", "singbox-up")
            self._down_since, self._down_alerted = None, False
            return
        if self._down_since is None:
            self._down_since = now
        elif not self._down_alerted and now - self._down_since >= self.cfg.down_alert:
            self._down_alerted = True
            self._notify(f"sing-box не работает больше {int(self.cfg.down_alert)} с "
                         f"(не из админки)" + (" — killswitch блокирует выход LAN."
                                               if self.killswitch_status()["active"]
                                               else " — killswitch не загружен, трафик LAN идёт напрямую."),
                         "singbox-down")

    # --- Служебное ------------------------------------------------------

    def _notify(self, text: str, key: str) -> None:
        if self.notifier is not None:
            self.notifier.send("singbox", text, key=key, ttl=0)
