"""Остановка/запуск sing-box из админки и killswitch (OpenWrt).

Остановка: `/etc/init.d/sing-box stop`. sing-box в режиме TUN (auto_route, auto_redirect)
при остановке сам снимает маршруты и правила nftables — трафик LAN идёт напрямую через
провайдера. Пока стоит флаг остановки (`stop_flag`, tmpfs — сбрасывается перезагрузкой),
apply-nodes.sh не применяет новый конфиг и не поднимает sing-box.

Killswitch — режим «при остановленном sing-box блокировать»: пока sing-box не работает
(остановлен кнопкой, упал, перезапускается), пересылка из LAN (`lan_devices`, пусто —
интерфейс lan из netifd) запрещена
отдельной таблицей nftables `inet nodes_tester_killswitch`. Доступ к самому роутеру
(админка, SSH, DNS) не блокируется. Состояние режима хранится в `state_file` и
применяется фоновым потоком раз в `interval` секунд.

Сторожевой поток также сообщает (notify, событие singbox), если sing-box не работает без
команды из админки дольше `down_alert` секунд, и когда он поднялся.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time

TABLE = "nodes_tester_killswitch"
_DEV_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,15}$")


class ControlError(RuntimeError):
    pass


class SingboxControl:
    def __init__(self, cfg, state_file: str, notifier=None, run=subprocess.run,
                 clock=time.monotonic):
        self.cfg = cfg                      # SingboxControlConfig
        self.state_file = state_file
        self.notifier = notifier
        self._run = run
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._down_since = None
        self._down_alerted = False
        self.last_error = None
        self.killswitch = bool(self._load_state().get("killswitch", False))

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

    def block_active(self) -> bool:
        try:
            return self._cmd(["nft", "list", "table", "inet", TABLE], timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def status(self) -> dict:
        avail = self.available
        return {
            "available": avail,
            "running": self.running() if avail else None,
            "stopped_by_admin": self.stopped_by_admin() if avail else False,
            "killswitch": self.killswitch,
            "blocking": self.block_active() if avail else False,
            "lan_devices": self.lan_devices() if avail else list(self.cfg.lan_devices),
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
            for _ in range(20):                     # до 10 с на завершение процесса
                if not self.running():
                    break
                time.sleep(0.5)
            self.enforce()
        self._notify("sing-box остановлен из админки — " + (
            "killswitch включён, интернет в LAN заблокирован." if self.killswitch
            else "трафик LAN идёт напрямую через провайдера."), "singbox-stop")
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
            for _ in range(20):                     # до 10 с на появление процесса
                if self.running():
                    break
                time.sleep(0.5)
            self.enforce()
        self._notify("sing-box запущен из админки.", "singbox-start")
        return self.status()

    def set_killswitch(self, on: bool) -> dict:
        self._require()
        with self._lock:
            self.killswitch = bool(on)
            self._save_state()
            self.enforce()
        self._notify(f"Killswitch {'включён' if on else 'выключен'}: при остановленном sing-box "
                     f"трафик LAN {'блокируется' if on else 'идёт напрямую'}.", "killswitch")
        return self.status()

    # --- Killswitch -----------------------------------------------------

    def enforce(self) -> None:
        """Блок нужен ⇔ killswitch включён и sing-box не работает."""
        if not self.available:
            return
        with self._lock:
            want = self.killswitch and not self.running()
            have = self.block_active()
            try:
                if want and not have:
                    self._add_block()
                elif have and not want:
                    res = self._cmd(["nft", "delete", "table", "inet", TABLE])
                    if res.returncode != 0:
                        raise ControlError(res.stderr.strip())
                self.last_error = None
            except (OSError, subprocess.SubprocessError, ControlError) as exc:
                self.last_error = f"nftables: {exc}"
                print(f"  [singbox] {self.last_error}")

    def lan_devices(self) -> list:
        """Интерфейсы LAN: из настройки или, если она пуста, l3_device интерфейса lan
        из netifd (br-lan, eth0 — зависит от роутера)."""
        if self.cfg.lan_devices:
            return [d for d in self.cfg.lan_devices if _DEV_RE.match(d)]
        try:
            res = self._cmd(["ubus", "call", "network.interface.lan", "status"], timeout=10)
            dev = json.loads(res.stdout or "{}").get("l3_device") if res.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            dev = None
        return [dev] if isinstance(dev, str) and _DEV_RE.match(dev) else []

    def _add_block(self) -> None:
        devs = self.lan_devices()
        if not devs:
            raise ControlError("интерфейс LAN не определён — задайте singbox_control.lan_devices")
        names = ", ".join(f'"{d}"' for d in devs)
        rules = (f"table inet {TABLE} {{\n"
                 f"  chain forward {{\n"
                 f"    type filter hook forward priority -5; policy accept;\n"
                 f"    iifname {{ {names} }} counter drop comment \"nodes-tester killswitch\"\n"
                 f"  }}\n}}\n")
        res = self._cmd(["nft", "-f", "-"], input_text=rules)
        if res.returncode != 0:
            raise ControlError(res.stderr.strip() or f"nft вернул {res.returncode}")

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
                self.enforce()
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
                         f"(не из админки)" + (" — killswitch блокирует LAN." if self.killswitch
                                               else " — трафик LAN идёт напрямую."), "singbox-down")

    # --- Служебное ------------------------------------------------------

    def _notify(self, text: str, key: str) -> None:
        if self.notifier is not None:
            self.notifier.send("singbox", text, key=key, ttl=0)

    def _load_state(self) -> dict:
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        from nodes_common.fileio import atomic_write_text
        atomic_write_text(self.state_file, json.dumps({"killswitch": self.killswitch}) + "\n")
