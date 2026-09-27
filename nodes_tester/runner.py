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
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .clash_api import ClashApiClient, ClashApiError
from .config import Config
from .identity import NodeIdentity, coarse_region, parse_node
from .logbuffer import LogRing, TeeStream
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

        # Админка (Фаза 2-3): живой лог, внеплановый прогон, статус.
        self.log = LogRing()
        self._pass_requested = threading.Event()
        self._request_lock = threading.Lock()
        self._progress = {"phase": "stopped", "processed": 0, "total": 0, "node": None}
        self._last_pass_result = None
        self._wait_until = None
        self._current_pass = 0
        # Сквозной номер прогона (meta.pass_seq): НЕ сбрасывается в полночь, в отличие от
        # посуточного pass_no. По нему считается пауза (backoff) нод в прогонах.
        self._pass_seq = 0
        self._current_day = ""
        self._running = False
        self._httpd = None
        self._banned: set = set()

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
        banned_n = 0
        self._present_crcs: set[str] = set()
        for leaf in self.clash.list_group_members(top):
            if leaf in exclude:
                continue
            ident = parse_node(leaf)
            if not ident.node_id:              # нет CRC → не leaf-нода (под-селектор/группа):
                skipped += 1                   # напр. старый двухуровневый nodes-tester
                continue
            # Присутствие в selector независимо от фильтров тест-плана. В частности,
            # banned-нода должна сохранять present/last_seen и сам флаг бана.
            self._present_crcs.add(ident.node_id)
            if ident.node_id in self._banned:  # ручной бан из админки — не тестируем
                banned_n += 1
                continue
            region = (manual.get(leaf, "other") if rg.recognition == "manually"
                      else coarse_region(ident.country))
            if region.lower() not in excl_regions:
                out.append((region, ident))
        if skipped:
            print(f"  [!] в '{top}' пропущено {skipped} членов без CRC — это не leaf-ноды "
                  f"(под-селекторы/группы). Плоский конфиг перегенерирован? nodes-tester "
                  f"должен содержать сами ноды, а не {{region}}-nodes-tester")
        if banned_n:
            print(f"  · пропущено забаненных нод (бан из админки): {banned_n}")
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
        # Перехват stdout/stderr в кольцевой буфер (для /api/logs админки), дублируя
        # в реальный терминал. Любая ошибка инициализации уже находится под finally.
        self._stdout_orig, self._stderr_orig = sys.stdout, sys.stderr
        sys.stdout = TeeStream(self.log, self._stdout_orig)
        sys.stderr = TeeStream(self.log, self._stderr_orig)

        self._running = True
        self._progress = {"phase": "initializing", "processed": 0, "total": 0, "node": None}
        self._base = self.cfg.run.for_group(self.cfg.testing_group.tag)
        self._tests_cache: dict = {}
        self._region_cache: dict = {}
        self._originals: dict[str, str] = {}
        self._backoff: dict = {}   # crc -> (until, until_pass, streak, reason), из storage

        try:
            self.clash.ping()
            if not self._base.tests_enabled:
                raise ValueError("run.default.tests_enabled пуст — нечего тестировать")

            if self.board is not None:         # TTL heavy-veto (двухуровневое тестирование)
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
                else:                              # нет метки за сегодня (первый запуск
                    pass_no = self.storage.max_pass_today(current_day)   # после апдейта)
            self._last_cleanup = time.monotonic()
            if self.monitor is not None:
                self.monitor.start()
            if self.collector is not None:
                self.collector.start()
            if self.cfg.dashboard.enabled:
                self._start_dashboard()
            while True:
                today = time.strftime("%Y-%m-%d")
                if today != current_day:           # наступила полночь → новый день, сброс
                    current_day, pass_no = today, 0
                pass_no += 1
                self._current_pass = pass_no
                self._current_day = current_day
                empty = False
                with self._request_lock:
                    self._pass_requested.clear()
                started = time.time()
                error = None
                try:
                    empty = self._run_pass(pass_no)
                except ClashApiError as exc:
                    print(f"[!] Прогон #{pass_no} прерван ошибкой Clash API: {exc}")
                    empty = True                       # API-сбой → тоже выдержим паузу
                    error = str(exc)
                self._last_pass_result = {"pass": pass_no, "started": started,
                                          "finished": time.time(),
                                          "outcome": "error" if error else "empty" if empty else "completed",
                                          "error": error}
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
                    self._wait_interruptible(_EMPTY_PASS_RETRY)   # пауза пустого прохода
                else:
                    self._wait_before_next_pass()
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
        finally:
            self._running = False
            self._progress = {**self._progress, "phase": "stopped", "node": None}
            self._wait_until = None
            if self.monitor is not None:
                self.monitor.stop()
            if self.collector is not None:
                self.collector.stop()
            self._stop_dashboard()
            if self.storage is not None:
                self.storage.cleanup()             # финальная очистка (VACUUM — отдельно, cron)
                self.storage.close()
            self._restore()
            # Вернуть реальные stdout/stderr последними — чтобы хвостовые print
            # (напр. «выбор восстановлен») тоже попали в лог.
            sys.stdout, sys.stderr = self._stdout_orig, self._stderr_orig

        return self.cfg.storage.db_file if self.storage is not None else ""

    def _should_continue(self, pass_no: int) -> bool:
        # Запрос из админки, пришедший во время последнего планового прохода, обязан
        # породить ещё один проход, а не потеряться при loop=false.
        return self._base.loop or pass_no < self._base.rounds or self._pass_requested.is_set()

    # --- Админка: внеплановый прогон, статус, встроенный сервер -----------

    def request_pass(self) -> bool:
        """Запросить внеплановый прогон (из админки). Прерывает текущее ожидание."""
        with self._request_lock:
            fresh = not self._pass_requested.is_set()
            self._pass_requested.set()
            return fresh

    def _wait_interruptible(self, seconds: float) -> bool:
        """Ждать до `seconds`, но вернуться раньше (True), если запрошен прогон."""
        self._progress = {**self._progress, "phase": "waiting", "node": None}
        self._wait_until = time.time() + max(seconds, 0)
        if self._pass_requested.wait(seconds):
            with self._request_lock:
                self._pass_requested.clear()
            self._wait_until = None
            return True
        self._wait_until = None
        return False

    def status(self) -> dict:
        """Живой статус тестера для /api/status админки."""
        out = {
            "running": self._running,
            "pass": self._current_pass,
            "day": self._current_day,
            "switching": bool(self.switcher is not None and self.cfg.switching.enabled),
            "monitor": bool(self.monitor is not None and self.monitor.is_alive()),
            "traffic": bool(self.collector is not None and self.collector.is_alive()),
            "rotation_bound": self._rotation_bound_active() if hasattr(self, "_base") else False,
            "regions": [],
            "next_rotation": None,
            "progress": dict(self._progress),
            "request_queued": self._pass_requested.is_set(),
            "last_pass_result": self._last_pass_result,
            "wait_until": self._wait_until,
        }
        if self.switcher is not None:
            for region in self.switcher.active_regions():
                out["regions"].append({
                    "region": region,
                    "active": self.switcher.active_node(region),
                })
            out["next_rotation"] = self.switcher.next_rotate_deadline()
        return out

    def _start_dashboard(self) -> None:
        """Поднять HTTP-сервер админки потоком-демоном внутри процесса тестера."""
        d = self.cfg.dashboard
        try:
            from dashboard.server import build_app
            from dashboard.webapp import make_server

            app = build_app(self.cfg, runner=self)
            self._httpd = make_server(app, d.host, d.port)
        except OSError as exc:
            self._httpd = None
            print(f"[!] админка не запущена ({d.host}:{d.port} занят?): {exc}")
            return
        threading.Thread(target=self._httpd.serve_forever,
                         name="dashboard", daemon=True).start()
        print(f"Админка: http://{d.host}:{d.port}/  "
              f"(write/control: {'включены' if d.token else 'выключены — задайте dashboard.token'})")

    def _stop_dashboard(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

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
            self._wait_interruptible(self._base.pass_pause)

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
            if self._wait_interruptible(min(wait, poll)):
                print("\n===== внеплановый прогон (запрос из админки) =====")
                return

    def _run_pass(self, pass_no: int) -> bool:
        """Один полный прогон. Список нод перечитывается из Clash API.
        Возвращает True, если прогон пустой (нод нет) — вызывающий выдержит retry-паузу,
        чтобы не крутить цикл вплотную (см. review.md P1)."""
        # Снимок состояния хранилища ДО перечисления: бан влияет на список нод (skip),
        # backoff — на пропуск в обходе, endpoints — на host-aware раскладку.
        self._backoff = {}
        self._progress = {"phase": "enumerating", "processed": 0, "total": 0, "node": None}
        self._endpoints: dict = {}
        self._banned = set()
        if self.storage is not None:
            self.storage.maybe_load_nodes(self.cfg.storage.nodes_file)
            self._backoff = self.storage.load_backoff()
            try:
                seq = int(self.storage.get_meta("pass_seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            self._pass_seq = max(self._pass_seq, seq) + 1
            self.storage.set_meta("pass_seq", self._pass_seq)
            self._endpoints = self.storage.endpoints_by_crc()
            self._banned = self.storage.banned_crcs()
        else:
            self._pass_seq += 1

        nodes = self._enumerate_nodes()
        if self.storage is not None:
            # Физическое присутствие ведём по ВСЕМ leaf-членам selector, включая бан и
            # исключённые регионы; test-фильтры не должны превращать ноду в «удалённую».
            self.storage.touch_seen(self._present_crcs)
            self.storage.reconcile_presence(self._present_crcs)
        if not nodes:
            print(f"[!] Прогон #{pass_no}: в группе '{self._top}' нет тестируемых нод — пропуск")
            return True
        print(f"\n===== Прогон #{pass_no}: нод {len(nodes)} =====")

        self._host_ep_last: dict = {}         # host -> {sig: monotonic} (зазор, лёгкая+тяжёлая)
        if self.board is not None:
            self.board.set_pass(pass_no)
            self.board.begin_pass()

        node_by_raw = {ident.raw: (region, ident) for region, ident in nodes}
        for index, (region, ident) in enumerate(self._order_by_host(nodes)):
            self._progress = {"phase": "testing", "processed": index,
                              "total": len(nodes), "node": ident.raw}
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
            self._progress = {**self._progress, "phase": "switching", "node": None}
            self.switcher.evaluate_all()
        self._progress = {**self._progress, "processed": self._progress["total"], "node": None}
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
        for index, (region, ident) in enumerate(targets):
            self._progress = {"phase": "heavy_testing", "processed": index,
                              "total": len(targets), "node": ident.raw}
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

    # --- Пауза (в прогонах) / карантин (во времени) по провалу gate, персистентно ---

    def _backed_off(self, crc: str) -> bool:
        if not self.cfg.cooldown.enabled or not crc:
            return False
        ent = self._backoff.get(crc)                # (until, until_pass, streak, reason)
        if not ent:
            return False
        until, until_pass, _streak, reason = ent
        if reason == "garbage":
            return time.time() < until              # карантин — по времени
        return self._pass_seq <= until_pass         # пауза — по сквозному номеру прогона

    def restricted_crcs(self) -> set:
        """CRC с действующей паузой/карантином по свежему состоянию БД (для админки).
        Истёкшая, но ещё не снятая запись garbage ограничением не считается."""
        if not self.cfg.cooldown.enabled or self.storage is None:
            return set()
        now = time.time()
        return {crc for crc, (until, until_pass, _s, reason) in self.storage.load_backoff().items()
                if (now < until if reason == "garbage" else self._pass_seq <= until_pass)}

    def _backoff_update(self, crc: str, gate: bool) -> None:
        # Backoff персистентен → нужен storage. Без него ноды тестируются каждый проход.
        if not self.cfg.cooldown.enabled or not crc or self.storage is None:
            return
        prev = self._backoff.get(crc)       # (until, until_pass, streak, reason) до обновления
        if gate:
            if crc in self._backoff:
                self.storage.clear_backoff(crc)      # нода жива — снять backoff/карантин
                self._backoff.pop(crc, None)
                self.storage.add_node_event(crc, "recovered")   # событие восстановления
            return
        cd = self.cfg.cooldown
        streak = (prev[2] if prev else 0) + 1
        max_skip = max(1, int(cd.max_skip))
        skip = min(2 ** (streak - 1), max_skip)             # 1, 2, 4, 8, … прогонов
        # Потолок паузы достигнут (или провалена проба после карантина) → карантин.
        garbage = skip >= max_skip or (prev is not None and prev[3] == "garbage")
        if garbage:
            until, until_pass = int(time.time() + cd.garbage_hours * 3600.0), None
            reason = "garbage"
        else:
            until, until_pass = None, self._pass_seq + skip   # пропустить skip прогонов
            reason = "backoff"
        self.storage.set_backoff(crc, until, streak, reason, until_pass=until_pass)
        # Событие ТОЛЬКО на переходе состояния: первый уход в паузу и вход в карантин —
        # чтобы node_events не пух на каждый повторный провал.
        if garbage and (prev is None or prev[3] != "garbage"):
            self.storage.add_node_event(crc, "garbage", reason, streak)
        elif prev is None:
            self.storage.add_node_event(crc, "backoff", reason, streak)
        self._backoff[crc] = (until or 0, until_pass or 0, streak, reason)
        if garbage:
            print(f"  · gate-провал {crc} → карантин на {cd.garbage_hours:g} ч")
        else:
            print(f"  · gate-провал {crc} → пауза #{streak}: пропуск {skip} прогон(ов)")

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
