"""Фоновый монитор активных боевых нод.

Крутится в отдельном потоке с коротким интервалом, независимо от замеров
тестера. По каждой активной ноде (из switcher):

  1. ДРЕЙФ ВЫБОРА — если боевые селекторы слетели (напр. после reload sing-box),
     возвращает выбор на активную ноду по цепочке (switcher.reassert_chain).
  2. ЗДОРОВЬЕ — самый надёжный сигнал «нода жива» это боевой трафик через неё.
     Логика:
       • идёт трафик (≥ silence_floor за окно) → нода заведомо жива, не трогаем;
       • «тихо» дольше silence_window → ВНЕПЛАНОВЫЙ ЗОНД: качаем ~1 МБ через socks
         (prober). Провал (нет ответа ИЛИ скорость < probe_min_mbps — значит ТСПУ
         режет трафик, delay такое не ловит) fails раз подряд → EMERGENCY.
         Обрыв закачки сразу перепроверяется вторым зондом: одиночные обрывы TLS
         после простоя бывают и у живых нод, страйк — только если не прошёл и повтор.

Порог трафика приравнен к порогу зонда (min_mbps × window): пассивный трафик и
активный зонд меряют одно — «держит ли нода ≥ min_mbps». Тогда throttled-нода
(delay проходит, но трафик зарезан) не «зависает» до планового замера: как только
через неё перестаёт идти достаточный трафик, её догоняет зонд.

Если активной ноды больше нет в sing-box (регенерация конфига сменила теги) — монитор
её пропускает: замену выбирает переключатель, как только runner перечитает состав нод.
"""

from __future__ import annotations

import threading
import time

from nodes_config.groups import DIRECT, FAILSAFE

from .api import ApiError
from .identity import parse_node


class ProductionMonitor:
    def __init__(self, cfg, api, switcher, prober, traffic, pause_check=None, on_tick=None):
        self.cfg = cfg                 # MonitorConfig
        self.api = api
        self.sw = switcher
        # prober(region, leaf) -> (ok: bool, mbps: float, inconclusive: bool, error: str).
        # inconclusive — туннель жив, но замер не показателен (429/limited, http-ошибка):
        # это НЕ throttle → сбрасываем страйки. ApiError prober пробрасывает наверх.
        self.prober = prober
        # traffic() -> {crc: накопительные байты боевого трафика} (TrafficCollector.user_bytes).
        self.traffic = traffic
        self.pause_check = pause_check or (lambda: False)
        # Доп. проверка на каждом тике вне окна применения (пересборка по деградации).
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Состояние по региону: тихий таймер, накопленные байты, страйки зонда.
        self._state: dict[str, dict] = {}
        # Счётчики traffic() на прошлом тике — для дельты между тиками.
        self._last_bytes: dict[str, int] | None = None
        # Наблюдение за failsafe-out (общее для групп без кандидатов); None — не нужно.
        self._failsafe: dict | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="prod-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Runner сразу после этого закрывает Storage и восстанавливает selector;
            # поэтому нельзя возвращаться с живым monitor worker.
            self._thread.join()
            self._thread = None

    def is_alive(self) -> bool:
        """Жив ли фоновый поток монитора (для /api/status админки)."""
        return self._thread is not None and self._thread.is_alive()

    # --- Внутреннее -----------------------------------------------------

    def _loop(self) -> None:
        # wait() возвращает True при stop() — тогда выходим; иначе тикаем по интервалу.
        while not self._stop.wait(self.cfg.interval):
            try:
                self._tick()
                if self.on_tick is not None and not self.pause_check():
                    self.on_tick()
            except Exception as exc:  # noqa: BLE001 — монитор не должен ронять процесс
                print(f"  [monitor] ошибка: {exc}")

    def _tick(self) -> None:
        if self.pause_check():
            return
        try:
            proxies = self.api.all_proxies()
        except ApiError:
            return  # API недоступен (sing-box рестартует) — пропускаем тик

        # Боевой трафик по CRC ноды за этот тик (свой трафик тестера/зонда исключаем).
        node_bytes = self._traffic_delta()
        now = time.monotonic()

        for region in self.sw.active_regions():
            node = self.sw.active_node(region)
            if not node or node not in proxies:
                # Активной ноды нет/исчезла — замену выберет переключатель.
                self._state.pop(region, None)
                continue

            if self.cfg.reassert_drift:
                self.sw.reassert_chain(region, proxies)

            st = self._state.setdefault(
                region, {"quiet_since": now, "quiet_bytes": 0, "strikes": 0}
            )
            crc = parse_node(node).node_id or node
            st["quiet_bytes"] += node_bytes.get(crc, 0)

            if st["quiet_bytes"] >= self.cfg.silence_floor_bytes:
                # Нода пассивно доказала пропускную способность — жива.
                st.update(quiet_since=now, quiet_bytes=0, strikes=0)
                continue
            if now - st["quiet_since"] < self.cfg.silence:
                continue  # ещё недостаточно тихо

            self._probe_region(region, node, st, now)

        self._watch_failsafe(proxies, node_bytes, now)

    def _watch_failsafe(self, proxies: dict, node_bytes: dict, now: float) -> None:
        """failsafe-out групп без кандидатов наблюдается как нода: трафик через выбранную им
        ноду — жив, тишина дольше silence — один зонд через тестовый селектор на все группы.
        Провал fails раз подряд — группы с direct_fallback уходят на direct-out; ответ —
        возвращаются на failsafe-out."""
        groups = {r: t for r, t in self.sw.fallback_regions() if t in (FAILSAFE, DIRECT)}
        if not groups or FAILSAFE not in proxies:
            self._failsafe = None
            return
        st = self._failsafe = self._failsafe or {"quiet_since": now, "quiet_bytes": 0, "strikes": 0}
        if FAILSAFE in groups.values():
            leaf = proxies[FAILSAFE].get("now") or ""
            st["quiet_bytes"] += node_bytes.get(parse_node(leaf).node_id or leaf, 0)
            if st["quiet_bytes"] >= self.cfg.silence_floor_bytes:
                st.update(quiet_since=now, quiet_bytes=0, strikes=0)
                return
        if now - st["quiet_since"] < self.cfg.silence:
            return
        st.update(quiet_since=now, quiet_bytes=0)
        region = next(iter(groups))
        try:
            ok, mbps, inconclusive, error = self.prober(region, FAILSAFE)
            if not ok and not inconclusive:          # одиночный обрыв — повтор, как у ноды
                ok, mbps, inconclusive, error = self.prober(region, FAILSAFE)
        except ApiError:
            return
        if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
            st["strikes"] = 0
            for region, target in groups.items():
                if target == DIRECT:
                    self.sw.failsafe_verdict(region, alive=True)
            return
        st["strikes"] += 1
        print(f"  [monitor] зонд failsafe-out провален ({error or f'{mbps:.2f} Мбит/с'}) "
              f"{st['strikes']}/{self.cfg.fails}")
        if st["strikes"] >= self.cfg.fails:
            st["strikes"] = 0
            for region, target in groups.items():
                if target == FAILSAFE:
                    self.sw.failsafe_verdict(region, alive=False)

    def _probe_region(self, region: str, node: str, st: dict, now: float) -> None:
        """Внеплановый зонд активной ноды: ~1 МБ через socks. Провал → страйк/emergency."""
        st.update(quiet_since=now, quiet_bytes=0)
        try:
            ok, mbps, inconclusive, error = self.prober(region, node)
            if not ok and not inconclusive:
                # Обрыв мог быть случайным (первое соединение после простоя) — повтор.
                ok, mbps, inconclusive, error = self.prober(region, node)
        except ApiError:
            return  # API/переключение недоступно — это НЕ смерть ноды, без страйка

        if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
            st["strikes"] = 0
            return

        st["strikes"] += 1
        why = f"нет ответа: {error or '?'}" if not ok else f"throttle {mbps:.2f} Мбит/с"
        print(f"  [monitor] {region}: зонд провален ({why}) "
              f"{st['strikes']}/{self.cfg.fails}")
        if st["strikes"] >= self.cfg.fails:
            print(f"  [monitor] {region}: активная нода не тянет трафик — EMERGENCY")
            self.sw.evaluate_region(region, emergency=True)
            st["strikes"] = 0

    def _traffic_delta(self) -> dict:
        """Дельта байт боевого трафика (up+down) по CRC ноды с прошлого тика."""
        now = self.traffic()
        last, self._last_bytes = self._last_bytes, now
        if last is None:                    # первый тик — точка отсчёта
            return {}
        return {crc: n - last.get(crc, 0) for crc, n in now.items() if n > last.get(crc, 0)}
