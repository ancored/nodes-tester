"""Фоновый монитор активных боевых нод.

Крутится в отдельном потоке с коротким интервалом, независимо от медленного
тест-прогона. По каждой активной ноде (из switcher):

  1. ДРЕЙФ ВЫБОРА — если боевые селекторы слетели (напр. после reload sing-box),
     возвращает выбор на активную ноду по цепочке (switcher.reassert_chain).
  2. ЗДОРОВЬЕ — пробит ноду через Clash API delay (sing-box сам дозванивается,
     не трогая socks/селекторы/прод). N провалов подряд → EMERGENCY-переключение.

Если активной ноды больше нет в Clash API (регенерация конфига сменила теги) —
монитор НИЧЕГО не делает: остаёмся на дефолтном выборе (в группах default —
это -failsafe urltest) и ждём первого прогона, который переберёт ноды и выберет
активные заново.
"""

from __future__ import annotations

import threading

from .clash_api import ClashApiError


class ProductionMonitor:
    def __init__(self, cfg, clash, switcher):
        self.cfg = cfg                 # MonitorConfig
        self.clash = clash
        self.sw = switcher
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._strikes: dict[str, int] = {}

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="prod-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --- Внутреннее -----------------------------------------------------

    def _loop(self) -> None:
        # wait() возвращает True при stop() — тогда выходим; иначе тикаем по интервалу.
        while not self._stop.wait(self.cfg.interval):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — монитор не должен ронять процесс
                print(f"  [monitor] ошибка: {exc}")

    def _tick(self) -> None:
        try:
            proxies = self.clash.all_proxies()
        except ClashApiError:
            return  # API недоступен (sing-box рестартует) — пропускаем тик

        for region in self.sw.active_regions():
            node = self.sw.active_node(region)
            if not node or node not in proxies:
                # Активной ноды нет/исчезла — остаёмся на дефолте, ждём прогона.
                self._strikes.pop(region, None)
                continue

            if self.cfg.reassert_drift:
                self.sw.reassert_chain(region, proxies)

            if self._probe(node):
                self._strikes[region] = 0
            else:
                self._strikes[region] = self._strikes.get(region, 0) + 1
                if self._strikes[region] >= self.cfg.fails:
                    print(f"  [monitor] {region}: активная нода не отвечает "
                          f"({self._strikes[region]}x) — EMERGENCY")
                    self.sw.evaluate_region(region, emergency=True)
                    self._strikes[region] = 0

    def _probe(self, node: str) -> bool:
        """True — нода жива (delay вернулся). Ошибка API ≠ смерть ноды."""
        try:
            delay = self.clash.delay(node, self.cfg.probe_url, self.cfg.probe_timeout)
        except ClashApiError:
            return True
        return delay is not None
