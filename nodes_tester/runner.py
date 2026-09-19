"""Оркестратор прогона.

Определение региона ноды — по region_groups.recognition:
  parse    — nodes-tester ПЛОСКИЙ (все ноды), регион из страны в имени ноды;
  manually — плоский nodes-tester, регион по спискам из конфига.
Тестер выбирает ноду прямо в nodes-tester (PUT nodes-tester = <нода>).

Порядок обхода — host-aware (анти-ТСПУ): очередь раскладывается так, чтобы ноды
одного хоста стояли максимально далеко, плюс зазор min_host_gap между обращениями к
одному хосту с РАЗНЫМ портом/протоколом. Групповых/межпрогонных пауз нет. Параметры
прогона послойные: default → testing_group → регион.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .clash_api import ClashApiClient, ClashApiError
from .config import Config
from .identity import NodeIdentity, coarse_region, parse_node
from .monitor import ProductionMonitor
from .proxy import make_session
from .scoreboard import Scoreboard
from .storage import Storage
from .switcher import Switcher
from .tests import TestContext, get_test_class
from .traffic import TrafficCollector


# Как часто гонять единый cleanup() БД в непрерывном режиме (VACUUM — отдельно, cron).
_CLEANUP_INTERVAL = 24 * 3600
# Пауза после пустого/сорванного прохода — чтобы не крутить цикл вплотную (пустой
# selector, недоступный API, просроченный rotate_deadline). См. review.md P1.
_EMPTY_PASS_RETRY = 30.0


def _traffic_provider_factory(storage, window_seconds: float, ttl: float = 60.0):
    """Провайдер агрегатов трафика {provider/country/protocol: bytes за окно} для
    балансировки ротации, с TTL-кэшем (ротация редкая — не бьём БД чаще раза в минуту)."""
    cache: dict = {"t": -1e9, "data": {}}

    def provider() -> dict:
        now = time.monotonic()
        if now - cache["t"] >= ttl:
            cache["data"] = storage.traffic_by_dims(window_seconds)
            cache["t"] = now
        return cache["data"]

    return provider


class Runner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.clash = ClashApiClient(cfg.clash_api)
        self._top = cfg.testing_group.selector.group
        self.board = None
        self.switcher = None
        self.monitor = None
        # Сериализует доступ к tester-селектору (self._top) и socks между плановым
        # прогоном (лёгкая/тяжёлая фазы) и внеплановым зондом монитора.
        self._tester_lock = threading.Lock()

        # Storage создаём РАНЬШЕ switcher/scoreboard: рейтинг и состояние переключений
        # теперь живут в БД (замена score.csv/switch_state.json).
        self.storage = None
        self.collector = None
        if cfg.storage.enabled:
            self.storage = Storage(cfg.storage)
            # Однократный импорт старых файлов из каталога БД (если ещё не в БД).
            db_dir = os.path.dirname(os.path.abspath(cfg.storage.db_file))
            self.storage.migrate_legacy(os.path.join(db_dir, "score.csv"),
                                        os.path.join(db_dir, "switch_state.json"))
            if cfg.storage.traffic.enabled:
                self.collector = TrafficCollector(
                    cfg.storage.traffic, self.clash, self.storage)

        if cfg.scoring.enabled and self.storage is not None:
            self.board = Scoreboard(self.storage, cfg.scoring)
            if cfg.switching.enabled:
                traffic_provider = None
                lb = cfg.switching.rotation.load_balance
                if lb.enabled:
                    if self.storage is not None:
                        traffic_provider = _traffic_provider_factory(
                            self.storage, lb.window_hours * 3600.0)
                    else:
                        print("  [switch] load_balance включён, но storage выключен — "
                              "балансировка по трафику недоступна")
                self.switcher = Switcher(cfg.switching, self.clash, self.board,
                                         self._top, traffic_provider, self.storage)
                if cfg.monitor.enabled:
                    self.monitor = ProductionMonitor(
                        cfg.monitor, self.clash, self.switcher,
                        prober=self._probe_node, tester_group=self._top,
                    )

    # --- Планирование --------------------------------------------------

    def _enumerate_nodes(self) -> list[tuple]:
        """[(region, NodeIdentity)] для всех тестируемых нод (плоский nodes-tester)."""
        top = self._top
        exclude = set(self.cfg.testing_group.selector.exclude) | {top}
        rg = self.cfg.region_groups
        excl_regions = {r.lower() for r in rg.exclude}
        manual = rg.manual_map() if rg.recognition == "manually" else {}
        out, skipped = [], 0
        for leaf in self.clash.list_group_members(top):
            if leaf in exclude:
                continue
            ident = parse_node(leaf)
            if not ident.node_id:              # нет CRC → не leaf-нода (под-селектор/группа):
                skipped += 1                   # напр. старый двухуровневый nodes-tester
                continue
            region = (manual.get(leaf, "other") if rg.recognition == "manually"
                      else coarse_region(ident.country))
            if region.lower() not in excl_regions:
                out.append((region, ident))
        if skipped:
            print(f"  [!] в '{top}' пропущено {skipped} членов без CRC — это не leaf-ноды "
                  f"(под-селекторы/группы). Плоский конфиг перегенерирован? nodes-tester "
                  f"должен содержать сами ноды, а не {{region}}-nodes-tester")
        return out

    # --- Host-aware обход (анти-ТСПУ) ----------------------------------

    def _host_of(self, ident: NodeIdentity) -> str:
        """Хост ноды (server из storage). Фолбэк без storage/server — провайдер/CRC."""
        ep = self._endpoints.get(ident.node_id)
        if ep and ep[0]:
            return ep[0]
        return f"prov:{ident.provider}" if ident.provider else f"crc:{ident.node_id}"

    def _ep_sig(self, ident: NodeIdentity) -> tuple:
        """Сигнатура эндпоинта (порт, протокол) — для условия зазора «тот же хост,
        но другой порт/протокол»."""
        ep = self._endpoints.get(ident.node_id)
        return ((ep[1] if ep else None), ident.protocol)

    def _order_by_host(self, items: list[tuple]) -> list[tuple]:
        """Разложить [(region, ident)] так, чтобы ноды одного хоста стояли максимально
        далеко: round-robin по корзинам-хостам (большие корзины первыми)."""
        buckets: dict[str, deque] = {}
        for it in items:
            buckets.setdefault(self._host_of(it[1]), deque()).append(it)
        dqs = sorted(buckets.values(), key=len, reverse=True)   # большие первыми
        ordered = []
        while any(dqs):
            for dq in dqs:
                if dq:
                    ordered.append(dq.popleft())
        return ordered

    def _respect_host_gap(self, host: str, sig: tuple, gap: float) -> None:
        """Выдержать min_host_gap до обращения к тому же хосту с ДРУГИМ (порт/протокол).
        Тот же (хост, порт, протокол) ожидания не вызывает. Затем отметить время."""
        eps = self._host_ep_last.setdefault(host, {})
        if gap > 0:
            diff = [t for s, t in eps.items() if s != sig]
            if diff:
                wait = gap - (time.monotonic() - max(diff))
                if wait > 0:
                    print(f"  … анти-ТСПУ пауза {wait:.0f}s (хост {host})")
                    time.sleep(wait)
        eps[sig] = time.monotonic()

    def _tests_for(self, tests_enabled: list[str]):
        """Инстансы тестов для набора tests_enabled (кэшируются)."""
        key = tuple(tests_enabled)
        if key not in self._tests_cache:
            built = []
            for name in tests_enabled:
                cls = get_test_class(name)
                if cls is None:
                    print(f"  [!] неизвестный тест '{name}' — пропущен")
                    continue
                built.append(cls(self.cfg.tests.get(name) or {}))
            self._tests_cache[key] = built
        return self._tests_cache[key]

    def _region_params(self, region: str):
        if region not in self._region_cache:
            self._region_cache[region] = self.cfg.run.for_region(self._base, region)
        return self._region_cache[region]

    # --- Прогон --------------------------------------------------------

    def run(self) -> str:
        self.clash.ping()

        self._base = self.cfg.run.for_group(self.cfg.testing_group.tag)
        self._tests_cache: dict = {}
        self._region_cache: dict = {}
        self._originals: dict[str, str] = {}
        self._backoff: dict = {}               # crc -> (until, streak, reason), из storage

        if not self._base.tests_enabled:
            raise ValueError("run.default.tests_enabled пуст — нечего тестировать")

        if self.board is not None:             # TTL heavy-veto (двухуровневое тестирование)
            self.board.set_heavy_veto_ttl(self._base.heavy_veto_hours * 3600)

        if not self._base.loop:
            mode = f"прогонов: {self._base.rounds}"
        elif self._rotation_bound_active():
            mode = "rotation_bound (прогон к сроку ротации), Ctrl+C для остановки"
        else:
            mode = "непрерывно, Ctrl+C для остановки"
        print(f"Группа '{self._top}' (recognition={self.cfg.region_groups.recognition}); "
              f"тесты: {', '.join(self._base.tests_enabled)}; {mode}")
        if self._base.loop and not self._rotation_bound_active() and self._base.pass_pause <= 0:
            print("  [!] loop без rotation_bound и pass_pause<=0 — прогоны идут вплотную "
                  "(пауза только host-gap внутри прохода). Задайте pass_pause при желании.")
        if self.monitor is not None:
            m = self.cfg.monitor
            print(f"Монитор активных нод: тик {m.interval}s, тишина {m.silence_window / 60:.0f} мин "
                  f"(<{m.silence_floor_bytes / 1_000_000:.0f} МБ) → зонд "
                  f"{m.probe_bytes / 1_000_000:.0f} МБ @ {m.probe_min_mbps} Мбит/с")
        if self.storage is not None:
            self.storage.load_nodes(self.cfg.storage.nodes_file)
            print(f"Хранилище: {self.cfg.storage.db_file}"
                  + (" + сбор трафика" if self.collector is not None else ""))

        # Номер прогона ПОСУТОЧНЫЙ: сбрасывается в полночь, внутри суток переживает
        # рестарт (день+счётчик в meta). Дата прогона берётся из ts результата;
        # в дашборде показывается как "DD/MM-NNN". current_day — локальная дата.
        current_day, pass_no = time.strftime("%Y-%m-%d"), 0
        if self.storage is not None:
            if self.storage.get_meta("pass_day") == current_day:
                try:
                    pass_no = int(self.storage.get_meta("pass_no") or 0)
                except (TypeError, ValueError):
                    pass_no = 0
            else:                                  # нет метки за сегодня (первый запуск
                pass_no = self.storage.max_pass_today(current_day)   # после апдейта)
        self._last_cleanup = time.monotonic()
        try:
            if self.monitor is not None:
                self.monitor.start()
            if self.collector is not None:
                self.collector.start()
            while True:
                today = time.strftime("%Y-%m-%d")
                if today != current_day:           # наступила полночь → новый день, сброс
                    current_day, pass_no = today, 0
                pass_no += 1
                empty = False
                try:
                    empty = self._run_pass(pass_no)
                except ClashApiError as exc:
                    print(f"[!] Прогон #{pass_no} прерван ошибкой Clash API: {exc}")
                    empty = True                       # API-сбой → тоже выдержим паузу
                if self.storage is not None:
                    self.storage.set_meta("pass_day", current_day)
                    self.storage.set_meta("pass_no", pass_no)     # посуточный номер
                if (self.storage is not None
                        and time.monotonic() - self._last_cleanup >= _CLEANUP_INTERVAL):
                    self.storage.cleanup()             # единая очистка БД (без VACUUM)
                    self._last_cleanup = time.monotonic()
                if not self._should_continue(pass_no):
                    break
                if empty:
                    time.sleep(_EMPTY_PASS_RETRY)       # не крутим цикл на пустом проходе
                else:
                    self._wait_before_next_pass()
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
        finally:
            if self.monitor is not None:
                self.monitor.stop()
            if self.collector is not None:
                self.collector.stop()
            if self.storage is not None:
                self.storage.cleanup()             # финальная очистка (VACUUM — отдельно, cron)
                self.storage.close()
            self._restore()

        return self.cfg.storage.db_file if self.storage is not None else ""

    def _should_continue(self, pass_no: int) -> bool:
        return True if self._base.loop else pass_no < self._base.rounds

    # --- Ожидание между прогонами -------------------------------------

    def _rotation_bound_active(self) -> bool:
        """rotation_bound реально включён? Нужны loop + switcher + вкл. ротация."""
        return (self._base.rotation_bound and self._base.loop
                and self.switcher is not None and self.cfg.switching.enabled
                and self.cfg.switching.rotation.enabled)

    def _wait_before_next_pass(self) -> None:
        if self._rotation_bound_active():
            self._wait_for_rotation()
        elif self._base.pass_pause > 0:
            print(f"\n===== пауза между прогонами {self._base.pass_pause}s =====")
            time.sleep(self._base.pass_pause)

    def _wait_for_rotation(self) -> None:
        """Спать до ближайшего срока ротации (switcher), потом вернуться → новый прогон.
        Опрос короткими шагами: реагируем на сдвиг дедлайна (напр. emergency из монитора
        передвинул rotate_deadline) и на Ctrl+C. Нет дедлайнов (регионы не активированы) —
        ждём один интервал ротации и пробуем снова."""
        poll = 30.0
        interval = self.cfg.switching.rotation.interval
        announced = None
        while True:
            target = self.switcher.next_rotate_deadline()
            now = time.time()
            wait = (target - now) if target is not None else interval
            if wait <= 0:
                print("\n===== rotation_bound: настал срок ротации → новый прогон =====")
                return
            if announced is None or abs(wait - announced) > poll:
                mins = wait / 60.0
                tgt = ("ближайшая ротация" if target is not None
                       else "регионы не активированы, повтор")
                print(f"\n===== rotation_bound: ждём {mins:.1f} мин ({tgt}) =====")
                announced = wait
            time.sleep(min(wait, poll))

    def _run_pass(self, pass_no: int) -> bool:
        """Один полный прогон. Список нод перечитывается из Clash API.
        Возвращает True, если прогон пустой (нод нет) — вызывающий выдержит retry-паузу,
        чтобы не крутить цикл вплотную (см. review.md P1)."""
        nodes = self._enumerate_nodes()
        if not nodes:
            print(f"[!] Прогон #{pass_no}: в группе '{self._top}' нет нод — пропуск")
            return True
        print(f"\n===== Прогон #{pass_no}: нод {len(nodes)} =====")

        # Снимок backoff на начало прогона (crc -> (until, streak, reason)): нода с
        # until в будущем не тестируется (backoff/карантин, персистентно, по времени).
        self._backoff = {}
        self._endpoints: dict = {}
        if self.storage is not None:
            self.storage.maybe_load_nodes(self.cfg.storage.nodes_file)
            self._backoff = self.storage.load_backoff()
            self._endpoints = self.storage.endpoints_by_crc()   # crc -> (server, port)
            # присутствие в selector → не даём retention удалить живые ноды (P0)
            self.storage.touch_seen(ident.node_id for _, ident in nodes)
            # журнал появления/выбытия нод (added/removed на каждом переходе)
            self.storage.reconcile_presence(ident.node_id for _, ident in nodes)
        self._host_ep_last: dict = {}         # host -> {sig: monotonic} (зазор, лёгкая+тяжёлая)
        if self.board is not None:
            self.board.set_pass(pass_no)
            self.board.begin_pass()

        node_by_raw = {ident.raw: (region, ident) for region, ident in nodes}
        for region, ident in self._order_by_host(nodes):   # host-aware обход
            params = self._region_params(region)
            if self._backed_off(ident.node_id):     # backoff/карантин — не тестируем
                if self.board is not None:
                    self.board.keep(ident.raw)   # чтобы end_pass не удалил строку/score
                continue
            self._respect_host_gap(self._host_of(ident), self._ep_sig(ident), params.min_host_gap)
            record = self._test_node(region, ident, pass_no,
                                     self._tests_for(params.tests_enabled), params)
            if self.storage is not None and self.cfg.storage.store_results:
                self.storage.add_results(record)
            gate = self._score_and_maybe_switch(region, ident, record)
            self._backoff_update(ident.node_id, gate)

        # Фаза 2 (двухуровневое): тяжёлый 50МБ download-veto только для кандидатов.
        self._run_heavy_pass(node_by_raw, pass_no)

        if self.board is not None:
            self.board.end_pass(pass_no)
        if self.switcher is not None:
            self.switcher.evaluate_all()
        return False

    def _heavy_test(self):
        """Инстанс тяжёлого download-теста (кэш). None — если тест не зарегистрирован."""
        if not hasattr(self, "_heavy_cache"):
            cls = get_test_class("heavy_download")
            self._heavy_cache = cls(self.cfg.tests.get("heavy_download") or {}) if cls else None
        return self._heavy_cache

    def _heavy_targets(self, node_by_raw: dict) -> list:
        """Кандидаты на тяжёлый download: топ-heavy_candidates по (лёгкому) score на
        регион + активная нода. Возвращает [(region, ident), …] без повторов."""
        targets, picked = [], set()
        for region in self.board.regions():
            hc = int(getattr(self._region_params(region), "heavy_candidates", 0) or 0)
            if hc <= 0:
                continue
            raws = [c["node"] for c in self.board.candidates(region)[:hc]]
            active = self.switcher.active_node(region) if self.switcher else None
            if active and active not in raws:
                raws.append(active)                 # активную проверяем всегда
            for raw in raws:
                if raw in picked:
                    continue
                picked.add(raw)
                ni = node_by_raw.get(raw)
                if ni is not None:
                    targets.append(ni)
        return targets

    def _run_heavy_pass(self, node_by_raw: dict, pass_no: int) -> None:
        if self.board is None:
            return
        heavy = self._heavy_test()
        if heavy is None:
            return
        targets = self._order_by_host(self._heavy_targets(node_by_raw))   # host-aware
        if not targets:
            return
        print(f"\n── Фаза 2 · тяжёлый download-veto — нод: {len(targets)} ──")
        for region, ident in targets:
            if self._backed_off(ident.node_id):     # backoff/карантин — не качаем
                continue
            params = self._region_params(region)
            self._respect_host_gap(self._host_of(ident), self._ep_sig(ident), params.min_host_gap)
            # Лок общий с зондом монитора; host-gap-пауза и запись в БД — вне лока.
            with self._tester_lock:
                self._remember(self._top)
                try:
                    self.clash.select(self._top, ident.raw)   # плоско: nodes-tester → нода
                except ClashApiError as exc:
                    print(f"  {ident.short()}: ОШИБКА переключения (heavy): {exc}")
                    continue
                if params.switch_delay > 0:
                    time.sleep(params.switch_delay)
                session = make_session(self.cfg.testing_group.connection)
                ctx = TestContext(session=session, node=ident.raw,
                                  default_timeout=params.request_timeout, region=region)
                try:
                    res = heavy.run(ctx).to_dict()
                except Exception as exc:  # noqa: BLE001 — сбой heavy не рушит проход
                    res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                finally:
                    session.close()
            ok = bool(res.get("ok"))
            self.board.set_heavy(ident.raw, ok)     # veto-фильтр (в скоринг НЕ идёт)
            rec = {"round": pass_no, "id": ident.node_id, "node": ident.raw,
                   "tests": {heavy.name: res}}
            if self.storage is not None and self.cfg.storage.store_results:
                self.storage.add_results(rec)
            if self.cfg.report.console:
                print(f"  {ident.short()}: heavy={'OK' if ok else 'VETO'} "
                      f"{res.get('speed_mbps', '-')}Mbps")

    def _score_and_maybe_switch(self, region: str, ident, record) -> bool:
        if self.board is None:
            return True                      # без борда gate неизвестен — не куладаунить
        _final, _s_run, gate = self.board.record(ident, region, record.get("tests", {}))
        if self.switcher is not None:
            self.switcher.notify_score(region, ident.raw, blocked=not gate)
        return gate

    # --- Backoff по провалу gate (единая ВРЕМЕННАЯ модель, персистентно) --------

    def _backed_off(self, crc: str) -> bool:
        if not self.cfg.cooldown.enabled or not crc:
            return False
        ent = self._backoff.get(crc)
        return bool(ent and time.time() < ent[0])   # ent = (until, streak, reason)

    def _backoff_update(self, crc: str, gate: bool) -> None:
        # Backoff персистентен → нужен storage. Без него ноды тестируются каждый проход.
        if not self.cfg.cooldown.enabled or not crc or self.storage is None:
            return
        prev = self._backoff.get(crc)                # (until, streak, reason) до обновления
        if gate:
            if crc in self._backoff:
                self.storage.clear_backoff(crc)      # нода жива — снять backoff/карантин
                self._backoff.pop(crc, None)
                self.storage.add_node_event(crc, "recovered")   # событие восстановления
            return
        cd = self.cfg.cooldown
        streak = (prev[1] if prev else 0) + 1
        secs = cd.base_seconds * (2 ** (streak - 1))         # удвоение за подряд провал
        cap = cd.garbage_hours * 3600.0
        garbage = secs >= cap or streak > cd.max_skip        # потолок → карантин (мусорная)
        if garbage:
            secs = cap
        until = int(time.time() + secs)
        reason = "garbage" if garbage else "backoff"
        self.storage.set_backoff(crc, until, streak, reason)
        # Событие ТОЛЬКО на переходе состояния: первый уход в backoff и вход в garbage —
        # чтобы node_events не пух на каждый повторный провал.
        if garbage and (prev is None or prev[2] != "garbage"):
            self.storage.add_node_event(crc, "garbage", reason, streak)
        elif prev is None:
            self.storage.add_node_event(crc, "backoff", reason, streak)
        self._backoff[crc] = (until, streak, reason)
        tag = "карантин (мусорная)" if garbage else f"backoff #{streak}"
        print(f"  · gate-провал {crc} → {tag}, пропуск ~{secs / 60:.0f} мин")

    def _probe_node(self, region: str, leaf: str) -> tuple[bool, float, bool]:
        """Внеплановый зонд активной ноды для монитора: закачка ~probe_bytes через socks.

        Возвращает (ok, mbps, inconclusive). inconclusive — туннель жив, но замер не
        показателен (429/limited, http-ошибка): монитор трактует как «не throttle», без
        страйка. ClashApiError (переключение/API) пробрасывает наверх — монитор пропустит.
        """
        mon = self.cfg.monitor
        url = mon.probe_url or f"https://speed.cloudflare.com/__down?bytes={mon.probe_bytes}"
        with self._tester_lock:
            self._remember(self._top)
            self.clash.select(self._top, leaf)     # ClashApiError → наверх (монитор пропустит)
            if self.cfg.run.default.switch_delay > 0:
                time.sleep(self.cfg.run.default.switch_delay)
            session = make_session(self.cfg.testing_group.connection)
            try:
                cls = get_test_class("download")
                test = cls({"url": url, "duration": mon.probe_timeout,
                            "connect_timeout": mon.probe_timeout})
                ctx = TestContext(session=session, node=leaf,
                                  default_timeout=mon.probe_timeout, region=region)
                res = test.run(ctx).to_dict()
            finally:
                session.close()
        ok = bool(res.get("ok"))
        mbps = float(res.get("speed_mbps", 0.0) or 0.0)
        inconclusive = bool(res.get("limited") or res.get("http_error"))
        return ok, mbps, inconclusive

    def _remember(self, tag: str) -> None:
        if not self._base.restore_selection or tag in self._originals:
            return
        try:
            self._originals[tag] = self.clash.current_selection(tag)
        except ClashApiError:
            self._originals[tag] = ""

    def _restore(self) -> None:
        for tag, sel in self._originals.items():
            if not sel:
                continue
            try:
                self.clash.select(tag, sel)
            except ClashApiError as exc:
                print(f"[!] Не удалось восстановить '{tag}' -> '{sel}': {exc}")
        if self._originals:
            print("Исходный выбор селекторов восстановлен.")

    def _test_node(self, region: str, ident: NodeIdentity, pass_no: int, tests, params) -> dict:
        leaf = ident.raw
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": pass_no,
            "provider": ident.provider,
            "protocol": ident.protocol,
            "region": ident.country,          # страна из имени
            "label": ident.label,             # доп. поле из тега (напр. AI)
            "id": ident.node_id,
            "region_group": region,           # коарс-регион
            "node": leaf,
        }

        # Плоский nodes-tester: PUT nodes-tester = leaf. Лок общий с внеплановым зондом
        # монитора — они не топчут селектор/socks друг друга.
        with self._tester_lock:
            self._remember(self._top)
            try:
                self.clash.select(self._top, leaf)
            except ClashApiError as exc:
                print(f"  {ident.short()}: ОШИБКА переключения: {exc}")
                base["tests"] = {"_select": {"ok": False, "error": str(exc)}}
                return base

            if params.switch_delay > 0:
                time.sleep(params.switch_delay)

            session = make_session(self.cfg.testing_group.connection)
            ctx = TestContext(session=session, node=leaf,
                              default_timeout=params.request_timeout, region=region)
            # Переключение удалось — фиксируем _select=OK ВСЕГДА (симметрично ветке
            # ошибки выше). Иначе строка _select пишется только при провале и «залипает»
            # в дашборде навсегда, не перекрываясь успехом (см. историю бага).
            results = {"_select": {"ok": True}}
            try:
                for test in tests:
                    if not test.due(pass_no):      # тяжёлые тесты — раз в N прогонов (every)
                        continue
                    try:
                        res = test.run(ctx).to_dict()
                    except Exception as exc:  # noqa: BLE001 — один тест не рушит проход/ноду
                        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    results[test.name] = res
                    if test.name == "connectivity" and not res.get("ok"):
                        break                      # нет связности → не гоняем остальное (трафик)
            finally:
                session.close()                    # session закрываем всегда

        base["tests"] = results
        if self.cfg.report.console:
            print(f"  {ident.short()}: {_summary(results)}")
        return base


def _summary(results: dict) -> str:
    """Краткая однострочная сводка по ноде для консоли."""
    parts = []
    conn = results.get("connectivity")
    if conn:
        if conn.get("ok"):
            parts.append(f"conn=OK[{conn.get('country', '?')} {conn.get('exit_ip', '')}]".strip())
        else:
            parts.append("conn=FAIL")
    lat = results.get("latency")
    if lat:
        parts.append(f"ttfb={lat.get('ttfb_ms', '-')}ms" if lat.get("ok") else "ttfb=FAIL")
    jit = results.get("jitter")
    if jit and jit.get("ok"):
        parts.append(f"jitter={jit.get('jitter_ms', '-')}ms loss={jit.get('loss_pct', '-')}%")
    dl = results.get("download")
    if dl:
        thr = dl.get("throttle_ratio", "-")
        if dl.get("limited"):
            parts.append(f"dl=LIMITED(thr={thr})")
        elif dl.get("ok"):
            parts.append(f"dl={dl.get('speed_mbps', '-')}Mbps thr={thr}")
        else:
            parts.append("dl=FAIL")
    reach = results.get("reachability")
    if reach:
        parts.append(f"reach={reach.get('reached', '-')}/{reach.get('total', '-')}")
    return "  ".join(parts) if parts else "нет результатов"
