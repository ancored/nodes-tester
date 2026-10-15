"""Оркестратор тестера.

nodes-tester плоский (все ноды); тестер выбирает ноду прямо в нём (PUT nodes-tester =
<нода>). Регионы ноды (для параметров тестов for_region) — по стране, которую измерил
conn-тест; страны в имени ноды нет.

Прогонов нет: у каждого теста свой срок годности (tests.<имя>.ttl, укороченный на долю
ttl_jitter), и тестер проверяет только ноды с истёкшими тестами — сначала активные ноды
групп и кандидатов резерва, затем остальные, начиная с самого старого результата. Не
успевает очередь — каждая нода просто освежается раз в полный обход.

Разброс ttl_jitter разводит сроки нод одного хоста; при равных сроках (новые ноды) первой
берётся нода хоста, к которому дольше всего не обращались. Между обращениями к одному хосту
с РАЗНЫМ портом/протоколом — зазор tester.host_gap (анти-ТСПУ).
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from datetime import datetime, timezone

from .api import ApiClient, ApiError
from .config import TESTS, Config, region_of
from .freshness import expires, fresh
from .identity import NodeIdentity, parse_node
from .logbuffer import LogRing, TeeStream
from .monitor import ProductionMonitor, singbox_log_path
from .notify import Notifier
from .singbox_ctl import SingboxControl
from .proxy import make_session
from .rebuild import RebuildWatch
from .reserve import ReserveKeeper
from .scoreboard import Scoreboard
from .storage import Storage
from .switcher import Switcher
from .tests import TestContext, get_test_class
from .traffic import TrafficCollector
from .whitelist import WhitelistCheck


# Как часто гонять единый cleanup() БД в непрерывном режиме (VACUUM — отдельно, cron).
_CLEANUP_INTERVAL = 24 * 3600
# Пауза после перезапуска sing-box посреди замера: ядро поднимает наборы правил и
# inbound-ы, прежде чем замерам можно верить.
_BOX_SETTLE = 60.0
# Самый долгий шаг ожидания: срок ротации, запрос из админки, смена состава нод.
_WAIT_STEP = 30.0
# Как часто перечитывать состав нод из sing-box (новые ноды, ушедшие, бан).
_REFRESH = 60.0
# Как часто освежать тип выходных IP и белые списки (у них свои сроки годности).
_SERVICE = 300.0
# Карантин по низкому рейтингу — только после стольких замеров (новую ноду не судим
# по одному замеру).
_LOW_SCORE_MIN_SAMPLES = 3
# Снятая для резерва из карантина нода, снова провалившая проверку, — повторно не раньше.
_RELEASE_RETRY = 6 * 3600.0


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


def _leaf_outbounds(path: str) -> dict:
    """Тег → outbound нод фрагмента (nodes.json, whnodes.json); нет файла — пусто."""
    try:
        with open(path, encoding="utf-8") as fh:
            fragment = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {ob.get("tag"): ob for ob in fragment.get("outbounds", []) + fragment.get("endpoints", [])
            if parse_node(str(ob.get("tag", ""))).node_id}


class Runner:
    def __init__(self, cfg: Config, once: bool = False):
        self.cfg = cfg
        self.once = once                        # проверить все ноды один раз и выйти
        self.api = ApiClient(cfg.api)
        self._top = cfg.tester.selector
        self.orchestrator = None
        self._apply_in_progress = False
        self._apply_pause_until = 0.0
        # Сериализует доступ к tester-селектору (self._top) и socks между тестером,
        # резервом и внеплановым зондом монитора.
        self._tester_lock = threading.Lock()
        # Тестируемые ноды и анти-ТСПУ зазоры: их читает и процесс резерва.
        self._node_by_raw: dict = {}
        self._endpoints: dict = {}
        self._host_ep_last: dict = {}           # хост → {(порт, протокол): monotonic}
        self._released_at: dict = {}            # crc → когда снята для резерва (monotonic)
        self._whitelist = None              # WhitelistCheck: списки и адреса в кэше
        self._phase_tests: dict = {}            # имя → инстанс теста резерва (обязательные, heavy)
        self._tests_cache: dict = {}            # имя → инстанс замера рейтинга
        # Последний результат каждого замера ноды: ключ → {тест: (ts, результат)}. По нему
        # считаются сроки и рейтинг (последние данные каждого теста).
        self._latest: dict = {}

        # Админка: живой лог, тестирование по запросу, статус.
        self.log = LogRing()
        self._force_requested = threading.Event()
        self._request_lock = threading.Lock()
        # Ноды, которые проверяются по запросу, не дожидаясь сроков (админка, --once).
        self._forced: set[str] = set()
        self._progress = {"phase": "stopped", "node": None}
        self._wait_until = None
        # Ключи нод селектора тестера на момент последней сверки (бан и исключённые — тоже).
        self._present_crcs: set[str] = set()
        # Нода из очереди пропала из селектора: состав перечитывается сразу, а не через _REFRESH.
        self._stale_nodes = False
        self._banned_n = 0
        # PID sing-box на момент сверки: провал замера после перезапуска ядра (скрипт
        # обновления, apply по cron) — не провал ноды.
        self._box_pid = None
        self._running = False
        self._httpd = None
        self._banned: set = set()
        self._backoff: dict = {}   # crc -> (until, streak, reason), из storage
        self._originals: dict[str, str] = {}
        self.notifier = Notifier(cfg.notify,
                                 fallback=self.via_tester if cfg.notify.via_tester else None)
        self.singbox = SingboxControl(cfg.singbox, self.notifier)

        # Storage создаём РАНЬШЕ switcher/scoreboard: рейтинг и состояние переключений
        # живут в БД. Монитору нужен боевой трафик по нодам — поток соединений держим и
        # без записи в БД (storage.traffic выключен).
        self.storage = Storage(cfg.storage)
        self.collector = TrafficCollector(cfg.storage.traffic, self.api,
                                          self.storage if cfg.storage.traffic.enabled else None)
        self.board = Scoreboard(self.storage, cfg.rating)
        self.board.set_policy(cfg.groups, cfg.regions)
        self.board.set_heavy_ttl(cfg.checks["heavy"]["ttl"], cfg.checks["heavy"]["ttl_jitter"])
        self.board.tier_floor = cfg.selection.tier_floor
        balance = cfg.selection.rotation.balance
        traffic_provider = (_traffic_provider_factory(self.storage, balance.window)
                            if balance.enabled else None)
        self.switcher = Switcher(cfg.selection, self.api, self.board, self._top,
                                 cfg.groups, traffic_provider, self.storage,
                                 server_of=lambda node: self._server(parse_node(node).node_id))
        self.switcher.on_activation = self._on_activation
        self.reserve = ReserveKeeper(self)
        self.rebuild = RebuildWatch(cfg, self.board, self.switcher, lambda: self.orchestrator,
                                    self.notifier)
        self.monitor = ProductionMonitor(
            cfg.monitor, self.api, self.switcher,
            prober=self._probe_node, traffic=self.collector.user_bytes,
            pause_check=self._apply_paused, hold_check=lambda: self._apply_in_progress,
            on_tick=self.rebuild.check,
            singbox_log=singbox_log_path(cfg.singbox.config),
        )

    # --- Состав нод ------------------------------------------------------

    def _enumerate_nodes(self) -> list[tuple]:
        """[(регионы, NodeIdentity)] для всех тестируемых нод (плоский nodes-tester).
        Регионы — по измеренной стране, для параметров тестов for_region."""
        top = self._top
        out = []
        banned_n = 0
        self._present_crcs = set()
        # Страна из базы: после рестарта тестера в рейтинге её ещё нет, а российская нода
        # не должна получить зарубежный набор тестов.
        stored = self.storage.node_countries()
        for leaf in self.api.list_group_members(top):
            ident = parse_node(leaf)
            if not ident.node_id:              # без ключа — не нода (direct, block, группа)
                continue
            # Присутствие в selector независимо от фильтров тест-плана. В частности,
            # banned-нода должна сохранять present/last_seen и сам флаг бана.
            self._present_crcs.add(ident.node_id)
            if ident.node_id in self._banned:  # ручной бан из админки — не тестируем
                banned_n += 1
                continue
            country = self.board.country(ident) or stored.get(ident.node_id, "")
            out.append((frozenset(region_of(country, self.cfg.regions)), ident))
        if banned_n != self._banned_n:
            print(f"  · пропущено забаненных нод (бан из админки): {banned_n}")
            self._banned_n = banned_n
        return out

    def _refresh_nodes(self) -> None:
        """Перечитать состав нод из sing-box и ограничения из БД. Новые ноды сразу попадают
        в базу (админка показывает их без замеров) и в очередь, ушедшие — в архив."""
        self.storage.maybe_load_nodes(self.cfg.nodes_file, self.cfg.raw_file("router"))
        self._backoff = self.storage.load_backoff()
        self._endpoints = self.storage.endpoints_by_crc()
        self._banned = self.storage.banned_crcs()
        self._box_pid = self.singbox.pid()
        nodes = self._enumerate_nodes()
        self._refresh_groups()
        # Физическое присутствие ведём по ВСЕМ leaf-членам selector, включая бан и
        # исключённые регионы; test-фильтры не должны превращать ноду в «удалённую».
        self.storage.touch_seen(self._present_crcs)
        self.storage.reconcile_presence(self._present_crcs)
        known = set(self._node_by_raw)
        self._node_by_raw = {ident.raw: (region, ident) for region, ident in nodes}
        self._forced &= set(self._node_by_raw)
        self.board.prune(set(self._node_by_raw))
        self.board.set_restricted(self._restricted_now() | self._banned)
        self.switcher.replace_missing(self._node_by_raw)
        added = set(self._node_by_raw) - known
        if known and added:
            print(f"  · в sing-box новые ноды: {len(added)}")
        self.reserve.wake(ratings_changed=bool(added))

    # --- Host-aware обход (анти-ТСПУ) ----------------------------------

    def _server(self, node_id: str) -> str:
        """Адрес сервера ноды из storage (нижний регистр); пусто — неизвестен."""
        ep = self._endpoints.get(node_id)
        return (ep[0] or "").lower() if ep else ""

    def _host_of(self, ident: NodeIdentity) -> str:
        """Хост ноды (server из storage). Фолбэк без storage/server — провайдер/CRC."""
        server = self._server(ident.node_id)
        if server:
            return server
        return f"prov:{ident.provider}" if ident.provider else f"crc:{ident.node_id}"

    def _ep_sig(self, ident: NodeIdentity) -> tuple:
        """Сигнатура эндпоинта (порт, протокол) — для условия зазора «тот же хост,
        но другой порт/протокол»."""
        ep = self._endpoints.get(ident.node_id)
        return ((ep[1] if ep else None), ident.protocol)

    def _host_wait(self, host: str, sig: tuple) -> float:
        """Сколько секунд ждать до обращения к хосту с этой сигнатурой (tester.host_gap):
        ждать нужно только после обращения к нему с ДРУГИМ портом/протоколом."""
        gap = self.cfg.tester.host_gap
        diff = [t for s, t in list(self._host_ep_last.get(host, {}).items()) if s != sig]
        if gap <= 0 or not diff:
            return 0.0
        return gap - (time.monotonic() - max(diff))

    def _respect_host_gap(self, host: str, sig: tuple) -> None:
        """Выдержать tester.host_gap до обращения к тому же хосту с ДРУГИМ (порт/протокол).
        Тот же (хост, порт, протокол) ожидания не вызывает. Затем отметить время."""
        wait = self._host_wait(host, sig)
        if wait > 0:
            print(f"  … анти-ТСПУ пауза {wait:.0f}s (хост {host})")
            time.sleep(wait)
        self._host_ep_last.setdefault(host, {})[sig] = time.monotonic()

    def _host_last(self, host: str) -> float:
        return max(list(self._host_ep_last.get(host, {}).values()), default=-math.inf)

    # --- Сроки тестов ----------------------------------------------------

    def _rating_test(self, name: str):
        """Замер рейтинга (config.tests) — инстансы создаются один раз."""
        if name not in self._tests_cache:
            self._tests_cache[name] = get_test_class(name)(self.cfg.tests.get(name) or {})
        return self._tests_cache[name]

    def _expiry(self, ident: NodeIdentity, name: str) -> float:
        """Срок последнего результата теста; 0 — теста ещё не было."""
        params = self.cfg.tests[name]
        ts = ((self._latest.get(ident.node_id) or {}).get(name) or (0,))[0]
        return expires(ts, params["ttl"], params["ttl_jitter"], f"{ident.node_id}/{name}")

    def _plan(self, raw: str, ident: NodeIdentity, now: float) -> tuple[float, list[str]]:
        """(когда ноду пора проверять — срок самого старого теста, какие тесты сейчас). За одно
        обращение идут и тесты, которым до срока меньше их разброса. connectivity — всегда,
        первым."""
        if raw in self._forced:
            return now, list(TESTS)
        expiry = {name: self._expiry(ident, name) for name in TESTS}
        start = min(expiry.values())
        if start > now:
            return start, []
        return start, [name for name in TESTS if name == "connectivity"
                       or expiry[name] <= now + self.cfg.tests[name]["ttl_jitter"]]

    def _pick(self, now: float):
        """Следующая нода к проверке: (регионы, нода, тесты) или срок, до которого ждать.
        Из готовых — активные ноды групп и кандидаты резерва, потом остальные; среди них — с
        самым ранним сроком, при равенстве — нода хоста, к которому дольше всего не
        обращались. Хост, у которого не выдержан host_gap, ждёт."""
        best, wake = None, now + _WAIT_STEP
        chosen = {self.switcher.active_node(g) for g in self.switcher.active_regions()}
        chosen |= {node for g in self.board.regions() for node in self.board.reserve(g)}
        for raw, (regions, ident) in self._node_by_raw.items():
            if self._backed_off(ident.node_id):    # пауза/карантин — не тестируем
                self._forced.discard(raw)
                continue
            start, tests = self._plan(raw, ident, now)
            if not tests:
                wake = min(wake, start)
                continue
            host = self._host_of(ident)
            wait = self._host_wait(host, self._ep_sig(ident))
            if wait > 0:
                wake = min(wake, now + wait)
                continue
            key = (raw not in chosen, start, self._host_last(host))
            if best is None or key < best[0]:
                best = (key, regions, ident, tests)
        return best[1:] if best else wake

    # --- Цикл ------------------------------------------------------------

    def _refresh_groups(self) -> None:
        """Состав групп переключения из sing-box: selector {tag}-auto-out каждой группы
        config.groups; члены — ноды (с ключом), запасные failsafe/block/direct — не ноды."""
        try:
            proxies = self.api.all_proxies()
        except ApiError as exc:
            print(f"  [!] состав групп не получен: {exc}")
            return
        members = {}
        for group in self.cfg.groups:
            info = proxies.get(f"{group.tag}-auto-out") or {}
            leaves = {m for m in info.get("all") or [] if parse_node(m).node_id}
            if leaves:
                members[group.tag] = leaves
        self.board.set_groups(members)
        if members:
            self.switcher.prune_groups(set(members))
        self.board.set_ip_info(self.storage.load_ip_info())

    def run(self) -> str:
        # Перехват stdout/stderr в кольцевой буфер (для /api/logs админки), дублируя
        # в реальный терминал. Любая ошибка инициализации уже находится под finally.
        self._stdout_orig, self._stderr_orig = sys.stdout, sys.stderr
        sys.stdout = TeeStream(self.log, self._stdout_orig)
        sys.stderr = TeeStream(self.log, self._stderr_orig)

        self._running = True
        self._progress = {"phase": "initializing", "node": None}

        try:
            self.api.ping()
            mode = "все ноды один раз" if self.once else "непрерывно, Ctrl+C для остановки"
            ttls = ", ".join(f"{name} {self.cfg.tests[name]['ttl'] / 60:g} мин" for name in TESTS)
            print(f"Группа '{self._top}'; сроки тестов: {ttls}; {mode}")
            m = self.cfg.monitor
            print(f"Монитор активных нод: тик {m.interval:.0f}s, тишина {m.silence / 60:.0f} мин "
                  f"(<{m.silence_floor_bytes / 1_000_000:.0f} МБ) → зонд "
                  f"{m.probe_mb:g} МБ @ {m.probe_min_mbps} Мбит/с")
            self.storage.maybe_load_nodes(self.cfg.nodes_file, self.cfg.raw_file("router"))
            print(f"Хранилище: {self.cfg.storage.path}"
                  + (" + сбор трафика" if self.cfg.storage.traffic.enabled else ""))
            self._latest = self.storage.latest_results(TESTS)
            if self.once:
                self._force_requested.set()
            self.monitor.start()
            self.reserve.start()
            self.collector.start()
            self._start_orchestrator()
            self.singbox.start_watch()
            if self.cfg.admin.enabled:
                self._start_dashboard()
            self._loop()
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
        finally:
            self._running = False
            self.singbox.stop_watch()
            self._progress = {**self._progress, "phase": "stopped", "node": None}
            self._wait_until = None
            self.monitor.stop()
            self.reserve.stop()
            self.collector.stop()
            self._stop_dashboard()
            if self.orchestrator is not None:
                self.orchestrator.stop()
            self.storage.cleanup()             # финальная очистка (VACUUM — отдельно, cron)
            self.storage.close()
            self._restore()
            # Вернуть реальные stdout/stderr последними — чтобы хвостовые print
            # (напр. «выбор восстановлен») тоже попали в лог.
            sys.stdout, sys.stderr = self._stdout_orig, self._stderr_orig

        return self.cfg.storage.path

    def _loop(self) -> None:
        refreshed = serviced = -math.inf
        cleaned = time.monotonic()
        while True:
            self._wait_apply_window()
            try:
                if self._stale_nodes or time.monotonic() - refreshed >= _REFRESH:
                    self._stale_nodes = False
                    self._refresh_nodes()
                    refreshed = time.monotonic()
                if self._force_requested.is_set():
                    with self._request_lock:
                        self._force_requested.clear()
                        self._forced = set(self._node_by_raw)
                    print(f"\n===== тестирование по запросу: нод {len(self._forced)} =====")
                self.switcher.rotate_due()
            except ApiError as exc:
                print(f"[!] API sing-box недоступен: {exc}")
                self._idle(time.time() + _WAIT_STEP)
                continue
            if time.monotonic() - serviced >= _SERVICE:
                self._service()
                serviced = time.monotonic()
            if time.monotonic() - cleaned >= _CLEANUP_INTERVAL:
                self.storage.cleanup()             # единая очистка БД (без VACUUM)
                cleaned = time.monotonic()
            if self.once and not self._forced and not self._force_requested.is_set():
                self._service()
                return
            pick = self._pick(time.time())
            if isinstance(pick, tuple):
                self._visit(*pick)
            elif self._idle(pick):
                refreshed = -math.inf          # запрос из админки: свежий состав нод

    def _service(self) -> None:
        """Тип выходных IP и белые списки — у них свои сроки годности."""
        self._refresh_ip_info([r.get("exit_ip") for r in self.board.rows.copy().values()])
        self._refresh_whitelist(list(self._node_by_raw.values()))

    def _visit(self, regions: frozenset, ident: NodeIdentity, tests: list[str]) -> None:
        self._progress = {"phase": "testing", "node": ident.raw}
        if self._measure(regions, ident, tests) is not None:
            self._forced.discard(ident.raw)

    def _measure(self, regions: frozenset, ident: NodeIdentity, tests) -> "bool | None":
        """Замерить ноду, записать результаты и пересчитать рейтинг. Возвращает gate или
        None — замер не засчитан (ноды нет в селекторе, sing-box перезапускается)."""
        self._respect_host_gap(self._host_of(ident), self._ep_sig(ident))
        record = self._test_node(regions, ident, tests)
        if record is None or self._apply_paused():     # замер мог попасть на перезапуск sing-box
            return None
        if self._box_interrupted(record):
            self._apply_pause_until = time.monotonic() + _BOX_SETTLE
            print("  · sing-box недоступен или перезапустился — замер не засчитан")
            return None
        self.storage.add_results(record)
        results = record["tests"]
        if "connectivity" in results:          # нода выбрана: рейтинг — по последним данным
            now = int(time.time())
            latest = self._latest.setdefault(ident.node_id, {})
            latest.update({name: (now, res) for name, res in results.items() if name in TESTS})
            results = {**{name: res for name, (_ts, res) in latest.items()},
                       "_select": results["_select"]}
        gate = self._score_and_maybe_switch(regions, ident, results)
        self._backoff_update(ident.node_id, gate, low=gate and self._low_score(ident.raw))
        self.switcher.after_measure(ident.raw)
        self.reserve.wake()                    # новый рейтинг может вытеснить кандидата резерва
        return gate

    # --- Админка: тестирование по запросу, статус, встроенный сервер -----

    def request_tests(self) -> bool:
        """Проверить все ноды сейчас, не дожидаясь сроков (из админки). False — такой запрос
        уже выполняется."""
        with self._request_lock:
            if self._force_requested.is_set() or self._forced:
                return False
            self._force_requested.set()
            return True

    def _idle(self, until: float) -> bool:
        """Ждать до `until` (не дольше _WAIT_STEP); True — пришёл запрос из админки."""
        seconds = min(max(until - time.time(), 0.0), _WAIT_STEP)
        self._progress = {"phase": "waiting", "node": None}
        self._wait_until = time.time() + seconds
        requested = self._force_requested.wait(seconds)
        self._wait_until = None
        return requested

    def status(self) -> dict:
        """Живой статус тестера для /api/status админки."""
        return {
            "running": self._running,
            "switching": True,
            "monitor": self.monitor.is_alive(),
            "traffic": self.collector.is_alive(),
            "regions": [{"region": region, "active": self.switcher.active_node(region)}
                        for region in self.switcher.active_regions()],
            "next_rotation": self.switcher.next_rotate_deadline(),
            "progress": dict(self._progress),
            "request_queued": self._force_requested.is_set() or bool(self._forced),
            "forced_left": len(self._forced),
            "wait_until": self._wait_until,
            "apply_paused": self._apply_paused(),
        }

    def _apply_start(self) -> None:
        self._apply_in_progress = True

    def _apply_end(self) -> None:
        """Конвейер применил конфиг: замеры ждут ещё 120 с, а выбор селекторов и состав нод
        восстанавливаются сразу — после перезапуска sing-box группы стоят на failsafe/block-out."""
        self._apply_in_progress = False
        self._apply_pause_until = time.monotonic() + 120
        self._stale_nodes = True
        self.monitor.reassert()

    def _apply_paused(self) -> bool:
        return self._apply_in_progress or time.monotonic() < self._apply_pause_until

    def _wait_apply_window(self) -> None:
        """Ждать конца паузы применения. Состав нод перечитывается, как только конвейер
        закончил: активная нода могла уйти из конфига, замена не должна ждать паузу."""
        while self._apply_paused():
            self._progress = {**self._progress, "phase": "apply_wait", "node": None}
            if self._stale_nodes and not self._apply_in_progress:
                self._stale_nodes = False
                try:
                    self._refresh_nodes()
                except ApiError:                   # ядро ещё поднимается — повтор через секунду
                    self._stale_nodes = True
            time.sleep(1)

    def _start_orchestrator(self) -> None:
        if not self.cfg.admin.token or not self.cfg.path:
            return
        from pathlib import Path
        from nodes_admin.orchestrator import Orchestrator
        try:
            self.orchestrator = Orchestrator(
                Path(self.cfg.path), Path.cwd(),
                on_apply_start=self._apply_start, on_apply_end=self._apply_end,
                on_finish=self._pipeline_finished,
            )
            self.orchestrator.start()
        except (OSError, ValueError) as exc:
            self.orchestrator = None
            print(f"[pipeline] оркестратор не запущен: {exc}")

    def _pipeline_finished(self, row: dict) -> None:
        """Прогон конвейера завершён: уведомления по журналу (откат, ошибка) и по raw
        (сбой подписки, срок и трафик подписки)."""
        if not row or not self.notifier.enabled:
            return
        try:
            with open(row.get("log_path") or "", encoding="utf-8", errors="replace") as fh:
                log_text = fh.read()
        except OSError:
            log_text = ""
        self.notifier.pipeline_finished(row, log_text)
        branch = {"router": "router", "fetch": "router", "clients": "clients"}.get(row.get("mode"))
        if row.get("dry_run") or branch is None or self.orchestrator is None:
            return
        try:
            with open(self.orchestrator.data_dir / "raw" / f"{branch}.json", encoding="utf-8") as fh:
                sources = json.load(fh).get("sources") or []
        except (OSError, ValueError, AttributeError):
            return
        self.notifier.subscriptions_state(sources, self.cfg.pipeline.fetch.keep_stale / 3600)

    def _start_dashboard(self) -> None:
        """Поднять HTTP-сервер админки потоком-демоном внутри процесса тестера."""
        d = self.cfg.admin
        try:
            from dashboard.server import build_app
            from dashboard.webapp import make_server

            app = build_app(self.cfg, runner=self)
            self._httpd = make_server(app, d.listen, d.port)
        except OSError as exc:
            self._httpd = None
            print(f"[!] админка не запущена ({d.listen}:{d.port} занят?): {exc}")
            return
        threading.Thread(target=self._httpd.serve_forever,
                         name="dashboard", daemon=True).start()
        print(f"Админка: http://{d.listen}:{d.port}/  "
              f"(правка и управление: {'включены' if d.token else 'выключены — задайте admin.token'})")

    def _stop_dashboard(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _box_interrupted(self, record: dict) -> bool:
        """Замер провалился из-за sing-box, а не ноды: не удалось выбрать ноду или нет
        связности, и при этом API не отвечает или процесс сменился с последней сверки.
        Новый PID запоминается — следующий провал засчитывается как обычно."""
        tests = record.get("tests") or {}
        selected = (tests.get("_select") or {}).get("ok")
        connected = (tests.get("connectivity") or {"ok": True}).get("ok")
        if selected and connected:
            return False
        try:
            self.api.ping()
        except ApiError:
            return True
        pid = self.singbox.pid()
        if pid is None:
            return False
        restarted = self._box_pid is not None and pid != self._box_pid
        self._box_pid = pid
        return restarted

    def _refresh_ip_info(self, ips) -> None:
        """Тип выходного IP для новых/устаревших exit_ip (один пакетный запрос)."""
        cfg = self.cfg.checks["ip_type"]
        stale = self.storage.ip_info_stale(ips, cfg["ttl"], cfg["ttl_jitter"])
        if not stale:
            return
        from .ipinfo import lookup
        infos = {}
        if cfg["via_tester"]:
            # Через SOCKS тестера (socks5h: имя резолвит нода): DNS-фильтр роутера часто
            # блокирует ip-api.com.
            infos = self.via_tester(lambda session: lookup(stale, cfg["url"], cfg["timeout"],
                                                           session=session)) or {}
        if not infos:
            infos = lookup(stale, cfg["url"], cfg["timeout"])
        self.storage.save_ip_info(infos)
        self.board.set_ip_info(self.storage.load_ip_info())
        print(f"  · тип выходного IP: обновлено {len(infos)} из {len(stale)}")

    def _refresh_whitelist(self, nodes) -> None:
        """Проверка «белый список» без свежего результата: тестируемые ноды и ноды клиентской
        ветви (whnodes.json), которых нет у роутера, — их тестер больше ничем не проверяет.
        Проверка локальная (списки и адрес сервера), трафик через ноду не идёт; неудача
        прежний вердикт не отменяет."""
        params = self.cfg.checks["whitelist"]
        last = self.storage.last_result_ts("whitelist")
        router = _leaf_outbounds(self.cfg.nodes_file)
        todo = [(ident.raw, ident.node_id, router[ident.raw], True) for _regions, ident in nodes
                if ident.raw in router]
        known = {ident.node_id for _r, ident in nodes} | {parse_node(t).node_id for t in router}
        for tag, ob in _leaf_outbounds(self.cfg.whnodes_file).items():
            key = parse_node(tag).node_id
            if key and key not in known:
                todo.append((tag, key, ob, False))
        todo = [t for t in todo if not fresh(last.get(t[1]), params["ttl"], params["ttl_jitter"],
                                             f"{t[1]}/whitelist")]
        if not todo:
            return
        if self._whitelist is None:
            self._whitelist = WhitelistCheck(params, self.cfg.singbox.config)
        passed = checked = 0
        for tag, key, ob, on_router in todo:
            res = self._whitelist.run(ob)
            if res.get("inconclusive"):
                print(f"  [whitelist] {tag}: {res['error']}")
                continue
            if on_router:
                self.board.set_required_result(tag, "whitelist", res["ok"])
            self.storage.add_results({"id": key, "node": tag, "tests": {"whitelist": res}})
            checked += 1
            passed += res["ok"]
        if checked:
            print(f"  · белые списки: проверено {checked}, проходят {passed}")

    def via_tester(self, fn, attempts: int = 3):
        """Выполнить fn(session) через SOCKS тестера на лучших здоровых нодах (по очереди,
        под общим локом тестового селектора). Первый непустой результат или None.
        Для служебных запросов, которым нужен прокси: с роутера напрямую они часто
        режутся (Telegram) или блокируются DNS-фильтром (ip-api.com)."""
        for node in self._healthy_nodes(attempts):
            with self._tester_lock:
                try:
                    self.api.select(self._top, node)
                except ApiError:
                    continue
                session = make_session(self.cfg.tester.socks)
                try:
                    result = fn(session)
                except Exception as exc:  # noqa: BLE001 — пробуем следующую ноду
                    print(f"  · через тестер ({node}) не удалось: {type(exc).__name__}")
                    result = None
                finally:
                    session.close()
            if result:
                return result
        return None

    def _healthy_nodes(self, limit: int) -> list:
        """Лучшие по рейтингу ноды без ограничений (для служебных запросов через тестер)."""
        seen, out = set(), []
        for group in self.board.regions():
            for cand in self.board.candidates(group):
                if cand["node"] not in seen:
                    seen.add(cand["node"])
                    out.append(cand)
        out.sort(key=lambda c: float(c.get("score") or 0), reverse=True)
        return [c["node"] for c in out[:limit]]

    # --- Проверки для резерва (nodes_tester.reserve) ---------------------

    def _on_activation(self, group: str, reason: str, node: str, prev) -> None:
        """Смена активной (или уход на failsafe): уведомление и добор резерва."""
        self.notifier.activation(group, reason, node, prev)
        self.reserve.wake()

    def reserve_size(self, group: str) -> int:
        g = self.cfg.group(group)
        return g.reserve if g is not None else self.cfg.selection.reserve

    def _phase_test(self, check: str):
        """Инстанс теста проверки (gemini, openai, anthropic, upstream, heavy) с её параметрами."""
        if check not in self._phase_tests:
            cls = get_test_class("heavy_download" if check == "heavy" else check)
            options = self.cfg.checks.get(check) or {}
            if check == "upstream":                 # outbound берётся из работающего конфига
                options = {**options, "singbox_config": self.cfg.singbox.config}
            self._phase_tests[check] = cls(options) if cls else None
        return self._phase_tests[check]

    def check_node(self, raw: str, check: str) -> None:
        """Обязательный тест группы (gemini, openai, anthropic, upstream) или "heavy" на одной ноде;
        вердикт — в рейтинг, замер — в results. Сетевой сбой обязательного теста
        (inconclusive) прошлый вердикт не отменяет."""
        node = self._node_by_raw.get(raw)
        test = self._phase_test(check)
        if node is None or test is None:
            return
        region, ident = node
        res = self._run_one(test, region, ident)
        if res is None:
            return
        ok = bool(res.get("ok"))
        if check == "heavy":
            # Отказ сервера (429/5xx, 401/403/404) — скорость не измерена: прежний вердикт
            # остаётся, резерв повторит проверку позже.
            if res.get("limited") or res.get("http_error"):
                verdict = "INCONCLUSIVE"
            else:
                self.board.set_heavy(raw, ok)
                verdict = "OK" if ok else "VETO"
            detail = f"{res.get('speed_mbps', '-')}Mbps {res.get('error') or ''}".strip()
        else:
            if not res.get("inconclusive"):
                self.board.set_required_result(raw, check, ok, res.get("country") or "")
            verdict = "OK" if ok else ("INCONCLUSIVE" if res.get("inconclusive") else "FAIL")
            detail = (f"{res.get('countries') or res.get('verdicts') or res.get('exit_ip') or ''} "
                      f"{res.get('error') or ''}").strip()
        self.storage.add_results({"id": ident.node_id, "node": raw, "tests": {test.name: res}})
        print(f"  [reserve] {ident.short()}: {test.name}={verdict} {detail}".rstrip())

    def release_for_reserve(self, group: str, skip: set) -> "str | None":
        """Резерву группы не хватает кандидатов: снять паузу или карантин с ноды группы,
        которая ограничена дольше всех, и сразу проверить её лёгкими тестами. Провал
        вернёт её в карантин с прежним счётчиком. Бан не снимается; нода, снятая и
        провалившаяся недавно (_RELEASE_RETRY), повторно не снимается.
        Возвращает снятую ноду (годится ли она — решит рейтинг) или None — снимать некого."""
        backoff = self.storage.load_backoff()
        since = self.storage.restricted_since()
        banned = self.storage.banned_crcs()
        now = time.monotonic()
        choices = []
        for raw in self.board.group_nodes(group):
            node = self._node_by_raw.get(raw)
            crc = node[1].node_id if node else ""
            entry = backoff.get(crc)
            if (raw in skip or not entry or crc in banned
                    or not self._in_effect(entry, time.time())
                    or now - self._released_at.get(crc, -_RELEASE_RETRY) < _RELEASE_RETRY):
                continue
            choices.append((since.get(crc, 0), raw))
        if not choices:
            return None
        _since, raw = min(choices)
        region, ident = self._node_by_raw[raw]
        crc = ident.node_id
        _until, streak, reason = backoff[crc]
        # Запись остаётся с прежним streak: провал gate вернёт ноду в карантин.
        self.storage.set_backoff(crc, int(time.time()), streak, reason)
        self._backoff[crc] = (int(time.time()), streak, reason)
        self.board.restrict(crc, False)
        self.storage.add_node_event(crc, "restriction_cleared", "reserve", streak)
        self._released_at[crc] = now
        print(f"  [reserve] {group}: кандидатов не хватает — снято ограничение с {ident.short()}")
        self._measure(region, ident, TESTS)
        return raw

    def _run_one(self, test, regions: frozenset, ident: NodeIdentity) -> "dict | None":
        """Один тест (обязательный, тяжёлый) на ноде через тестовый селектор.
        None — переключиться на ноду не удалось. Лок общий с зондом монитора;
        host-gap-пауза — до лока, запись в БД — у вызывающего, вне лока."""
        self._respect_host_gap(self._host_of(ident), self._ep_sig(ident))
        with self._tester_lock:
            self._remember(self._top)
            try:
                self.api.select(self._top, ident.raw)
            except ApiError as exc:
                print(f"  {ident.short()}: ОШИБКА переключения ({test.name}): {exc}")
                return None
            if self.cfg.tester.switch_delay > 0:
                time.sleep(self.cfg.tester.switch_delay)
            session = make_session(self.cfg.tester.socks)
            ctx = TestContext(session=session, node=ident.raw,
                              default_timeout=self.cfg.tester.request_timeout, regions=regions)
            try:
                return test.run(ctx).to_dict()
            except Exception as exc:  # noqa: BLE001 — сбой теста не рушит тестер
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                session.close()

    def _score_and_maybe_switch(self, regions: frozenset, ident, tests: dict) -> bool:
        _final, _s_run, gate = self.board.record(ident, ",".join(sorted(regions)), tests)
        # Нода может быть активной в нескольких группах — каждой нужна замена.
        for group in self.switcher.active_regions():
            if self.switcher.active_node(group) == ident.raw:
                self.switcher.notify_score(group, ident.raw, blocked=not gate)
        return gate

    # --- Пауза / карантин по провалу gate, персистентно ---

    @staticmethod
    def _in_effect(entry: tuple, now: float) -> bool:
        """Запись backoff (until, streak, reason) ещё действует."""
        return now < entry[0]

    def _backed_off(self, crc: str) -> bool:
        if not crc:
            return False
        entry = self._backoff.get(crc)
        return bool(entry) and self._in_effect(entry, time.time())

    def restricted_crcs(self) -> set:
        """CRC с действующей паузой/карантином по свежему состоянию БД (для админки).
        Истёкшая, но ещё не снятая запись ограничением не считается."""
        return self._restricted_now(self.storage.load_backoff())

    def _restricted_now(self, backoff: "dict | None" = None) -> set:
        """CRC с действующей паузой/карантином по снимку backoff (по умолчанию — последней сверки)."""
        if backoff is None:
            backoff = self._backoff
        now = time.time()
        return {crc for crc, entry in backoff.items() if self._in_effect(entry, now)}

    def restrict_node(self, crc: str, on: bool, entry: "tuple | None" = None) -> None:
        """Ручной карантин/бан (on) или снятие (off) из админки: сразу убрать ноду из
        кандидатов переключателя и, если она активна в какой-то группе, заменить её."""
        if on and entry is not None:
            self._backoff[crc] = entry          # тестер тоже её пропустит
        elif not on:
            self._backoff.pop(crc, None)
        self.board.restrict(crc, on)
        if on:
            for group in self.switcher.active_regions():
                active = self.switcher.active_node(group)
                if active and parse_node(active).node_id == crc:
                    self.switcher.evaluate_region(group, emergency=True)

    def _low_score(self, raw: str) -> bool:
        """Живая, но слабая нода: рейтинг ниже selection.quarantine.low_score (вкл. при > 0).
        Активные и новые (< _LOW_SCORE_MIN_SAMPLES замеров) не трогаем."""
        threshold = float(self.cfg.selection.quarantine.low_score or 0)
        row = self.board.get(raw) if threshold > 0 else None
        if not row or int(row.get("active") or 0):
            return False
        return (int(row.get("samples") or 0) >= _LOW_SCORE_MIN_SAMPLES
                and float(row.get("score") or 0) < threshold)

    def _backoff_update(self, crc: str, gate: bool, low: bool = False) -> None:
        """Провал gate — пауза: после n-го провала подряд нода не тестируется
        2^(n-1) сроков connectivity; дорос пропуск до quarantine.after_skips — карантин."""
        if not crc:
            return
        prev = self._backoff.get(crc)       # (until, streak, reason) до обновления
        cd = self.cfg.selection.quarantine
        if low:                             # gate пройден, но рейтинг ниже порога → карантин
            streak = (prev[1] if prev else 0) + 1
            until = int(time.time() + cd.duration)
            self.storage.set_backoff(crc, until, streak, "garbage")
            if prev is None or prev[2] != "garbage":
                self.storage.add_node_event(crc, "garbage", "low-score", streak)
            self._backoff[crc] = (until, streak, "garbage")
            self.board.restrict(crc, True)
            print(f"  · рейтинг ниже {cd.low_score:g}: {crc} → карантин на {cd.duration / 3600:g} ч")
            return
        if gate:
            if crc in self._backoff:
                self.storage.clear_backoff(crc)      # нода жива — снять backoff/карантин
                self._backoff.pop(crc, None)
                self.board.restrict(crc, False)
                self.storage.add_node_event(crc, "recovered")   # событие восстановления
            return
        streak = (prev[1] if prev else 0) + 1
        max_skip = max(1, int(cd.after_skips))
        skip = min(2 ** (streak - 1), max_skip)             # 1, 2, 4, 8, … сроков
        # Потолок паузы достигнут (или провалена проба после карантина) → карантин.
        garbage = skip >= max_skip or (prev is not None and prev[2] == "garbage")
        pause = cd.duration if garbage else skip * self.cfg.tests["connectivity"]["ttl"]
        until = int(time.time() + pause)
        reason = "garbage" if garbage else "backoff"
        self.storage.set_backoff(crc, until, streak, reason)
        # Событие ТОЛЬКО на переходе состояния: первый уход в паузу и вход в карантин —
        # чтобы node_events не пух на каждый повторный провал.
        if garbage and (prev is None or prev[2] != "garbage"):
            self.storage.add_node_event(crc, "garbage", reason, streak)
        elif prev is None:
            self.storage.add_node_event(crc, "backoff", reason, streak)
        self._backoff[crc] = (until, streak, reason)
        self.board.restrict(crc, True)
        if garbage:
            print(f"  · gate-провал {crc} → карантин на {cd.duration / 3600:g} ч")
        else:
            print(f"  · gate-провал {crc} → пауза #{streak}: {pause / 3600:g} ч")

    def _probe_node(self, group: str, leaf: str) -> tuple[bool, float, bool, str]:
        """Внеплановый зонд активной ноды группы для монитора: закачка ~probe_mb через socks.

        Возвращает (ok, mbps, inconclusive, error). inconclusive — туннель жив, но замер не
        показателен (429/limited, http-ошибка): монитор трактует как «не throttle», без
        страйка. ApiError (переключение/API) пробрасывает наверх — монитор пропустит.
        """
        mon = self.cfg.monitor
        url = mon.probe_url or f"https://speed.cloudflare.com/__down?bytes={int(mon.probe_mb * 1_000_000)}"
        with self._tester_lock:
            self._remember(self._top)
            self.api.select(self._top, leaf)     # ApiError → наверх (монитор пропустит)
            if self.cfg.tester.switch_delay > 0:
                time.sleep(self.cfg.tester.switch_delay)
            session = make_session(self.cfg.tester.socks)
            try:
                cls = get_test_class("download")
                test = cls({"url": url, "duration": mon.probe_timeout,
                            "connect_timeout": mon.probe_timeout})
                ctx = TestContext(session=session, node=leaf, default_timeout=mon.probe_timeout)
                res = test.run(ctx).to_dict()
            finally:
                session.close()
        ok = bool(res.get("ok"))
        mbps = float(res.get("speed_mbps", 0.0) or 0.0)
        inconclusive = bool(res.get("limited") or res.get("http_error"))
        return ok, mbps, inconclusive, str(res.get("error") or "")

    def _remember(self, tag: str) -> None:
        if tag in self._originals:
            return
        try:
            self._originals[tag] = self.api.current_selection(tag)
        except ApiError:
            self._originals[tag] = ""

    def _restore(self) -> None:
        for tag, sel in self._originals.items():
            if not sel:
                continue
            try:
                self.api.select(tag, sel)
            except ApiError as exc:
                print(f"[!] Не удалось восстановить '{tag}' -> '{sel}': {exc}")
        if self._originals:
            print("Исходный выбор селекторов восстановлен.")

    def _test_node(self, regions, ident: NodeIdentity, tests) -> "dict | None":
        """Замеры рейтинга ноды (tests, в порядке config.TESTS); None — ноды уже нет в
        селекторе (конфиг sing-box сменился после перечисления), оценивать нечего."""
        leaf = ident.raw
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": ident.provider,
            "protocol": ident.protocol,
            "id": ident.node_id,
            "regions": sorted(regions),        # регионы по измеренной стране
            "node": leaf,
        }

        # Плоский nodes-tester: PUT nodes-tester = leaf. Лок общий с внеплановым зондом
        # монитора — они не топчут селектор/socks друг друга.
        with self._tester_lock:
            self._remember(self._top)
            try:
                self.api.select(self._top, leaf)
            except ApiError as exc:
                if "not found in selector" in str(exc):
                    print(f"  {ident.short()}: ноды нет в селекторе (конфиг сменился) — пропуск")
                    self._stale_nodes = True       # перечитать состав до следующего выбора
                    return None
                print(f"  {ident.short()}: ОШИБКА переключения: {exc}")
                base["tests"] = {"_select": {"ok": False, "error": str(exc)}}
                return base

            if self.cfg.tester.switch_delay > 0:
                time.sleep(self.cfg.tester.switch_delay)

            session = make_session(self.cfg.tester.socks)
            ctx = TestContext(session=session, node=leaf,
                              default_timeout=self.cfg.tester.request_timeout, regions=regions)
            # Переключение удалось — фиксируем _select=OK ВСЕГДА (симметрично ветке
            # ошибки выше). Иначе строка _select пишется только при провале и «залипает»
            # в дашборде навсегда, не перекрываясь успехом (см. историю бага).
            results = {"_select": {"ok": True}}
            try:
                for name in (n for n in TESTS if n in tests):
                    try:
                        res = self._rating_test(name).run(ctx).to_dict()
                    except Exception as exc:  # noqa: BLE001 — один тест не рушит ноду
                        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    results[name] = res
                    if name == "connectivity" and not res.get("ok"):
                        break                      # нет связности → не гоняем остальное (трафик)
                    if name == "connectivity" and not ctx.regions and res.get("country"):
                        # Страна ещё не была известна: остальные тесты — по только что
                        # измеренной (российская нода — российским набором с первого раза).
                        ctx.regions = frozenset(region_of(res["country"], self.cfg.regions))
                        base["regions"] = sorted(ctx.regions)
            finally:
                session.close()                    # session закрываем всегда

        base["tests"] = results
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
