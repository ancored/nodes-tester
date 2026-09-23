"""Фоновый монитор активных боевых нод.

Крутится в отдельном потоке с коротким интервалом, независимо от медленного
тест-прогона. По каждой активной ноде (из switcher):

  1. ДРЕЙФ ВЫБОРА — если боевые селекторы слетели (напр. после reload sing-box),
     возвращает выбор на активную ноду по цепочке (switcher.reassert_chain).
  2. ЗДОРОВЬЕ — самый надёжный сигнал «нода жива» это боевой трафик через неё.
     Логика:
       • идёт трафик (≥ silence_floor за окно) → нода заведомо жива, не трогаем;
       • «тихо» дольше silence_window → ВНЕПЛАНОВЫЙ ЗОНД: качаем ~1 МБ через socks
         (prober). Провал (нет ответа ИЛИ скорость < probe_min_mbps — значит ТСПУ
         режет трафик, delay такое не ловит) fails раз подряд → EMERGENCY.

Порог трафика приравнен к порогу зонда (min_mbps × window): пассивный трафик и
активный зонд меряют одно — «держит ли нода ≥ min_mbps». Тогда throttled-нода
(delay проходит, но трафик зарезан) не «зависает» до планового прогона: как только
через неё перестаёт идти достаточный трафик, её догоняет зонд.

Если активной ноды больше нет в Clash API (регенерация конфига сменила теги) —
монитор НИЧЕГО не делает: остаёмся на дефолтном выборе и ждём первого прогона.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from .clash_api import ClashApiError
from .identity import parse_node


class ProductionMonitor:
    def __init__(self, cfg, clash, switcher, prober, tester_group):
        self.cfg = cfg                 # MonitorConfig
        self.clash = clash
        self.sw = switcher
        # prober(region, leaf) -> (ok: bool, mbps: float, inconclusive: bool).
        # inconclusive — туннель жив, но замер не показателен (429/limited, http-ошибка):
        # это НЕ throttle → сбрасываем страйки. ClashApiError prober пробрасывает наверх.
        self.prober = prober
        self.tester_group = tester_group
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Состояние по региону: тихий таймер, накопленные байты, страйки зонда.
        self._state: dict[str, dict] = {}
        # Кумулятивные байты по id соединения — для дельта-учёта между тиками.
        self._committed: dict[str, tuple[int, int]] = {}

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
            except Exception as exc:  # noqa: BLE001 — монитор не должен ронять процесс
                print(f"  [monitor] ошибка: {exc}")

    def _tick(self) -> None:
        try:
            conns = self.clash.connections()
            proxies = self.clash.all_proxies()
        except ClashApiError:
            return  # API недоступен (sing-box рестартует) — пропускаем тик

        # Боевой трафик по CRC ноды за этот тик (свой трафик тестера/зонда исключаем).
        node_bytes = self._traffic_delta(conns)
        now = time.monotonic()

        for region in self.sw.active_regions():
            node = self.sw.active_node(region)
            if not node or node not in proxies:
                # Активной ноды нет/исчезла — остаёмся на дефолте, ждём прогона.
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
            if now - st["quiet_since"] < self.cfg.silence_window:
                continue  # ещё недостаточно тихо

            self._probe_region(region, node, st, now)

    def _probe_region(self, region: str, node: str, st: dict, now: float) -> None:
        """Внеплановый зонд активной ноды: ~1 МБ через socks. Провал → страйк/emergency."""
        st.update(quiet_since=now, quiet_bytes=0)
        try:
            ok, mbps, inconclusive = self.prober(region, node)
        except ClashApiError:
            return  # API/переключение недоступно — это НЕ смерть ноды, без страйка

        if inconclusive or (ok and mbps >= self.cfg.probe_min_mbps):
            st["strikes"] = 0
            return

        st["strikes"] += 1
        why = "нет ответа" if not ok else f"throttle {mbps:.2f} Мбит/с"
        print(f"  [monitor] {region}: зонд провален ({why}) "
              f"{st['strikes']}/{self.cfg.fails}")
        if st["strikes"] >= self.cfg.fails:
            print(f"  [monitor] {region}: активная нода не тянет трафик — EMERGENCY")
            self.sw.evaluate_region(region, emergency=True)
            st["strikes"] = 0

    def _traffic_delta(self, conns: list) -> dict:
        """Дельта байт (up+down) по CRC ноды с прошлого тика; свой трафик исключаем."""
        out: dict[str, int] = defaultdict(int)
        seen = set()
        for c in conns:
            cid = c.get("id")
            if not cid:
                continue
            seen.add(cid)
            up, down = int(c.get("upload", 0)), int(c.get("download", 0))
            pup, pdown = self._committed.get(cid, (0, 0))
            self._committed[cid] = (up, down)
            dup, ddown = up - pup, down - pdown
            if dup <= 0 and ddown <= 0:
                continue
            chains = c.get("chains") or []
            if any(self.tester_group in x for x in chains):
                continue  # собственный трафик тестера/зонда — не считаем боевым
            leaf = chains[0] if chains else ""
            crc = parse_node(leaf).node_id or leaf or "?"
            out[crc] += max(0, dup) + max(0, ddown)
        # Забываем закрытые соединения.
        for cid in list(self._committed):
            if cid not in seen:
                del self._committed[cid]
        return out
