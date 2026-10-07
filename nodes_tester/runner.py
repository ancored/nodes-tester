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

import json
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .api import ApiClient, ApiError
from .config import Config
from .identity import NodeIdentity, coarse_region, parse_node
from .logbuffer import LogRing, TeeStream
from .monitor import ProductionMonitor
from .notify import Notifier
from .singbox_ctl import SingboxControl
from .proxy import make_session
from .reserve import ReserveKeeper
from .scoreboard import Scoreboard
from .storage import Storage
from .switcher import Switcher
from .tests import TestContext, get_test_class
from .traffic import TrafficCollector


# Как часто гонять единый cleanup() БД в непрерывном режиме (VACUUM — отдельно, cron).
_CLEANUP_INTERVAL = 24 * 3600
# Пауза после пустого/сорванного прохода — чтобы не крутить цикл вплотную (пустой
# selector, недоступный API, просроченный rotate_deadline).
_EMPTY_PASS_RETRY = 30.0
# Карантин по низкому рейтингу — только после стольких замеров (новую ноду не судим
# по одному прогону).
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


class Runner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api = ApiClient(cfg.box_api)
        self._top = cfg.testing_group.selector.group
        self.board = None
        self.switcher = None
        self.monitor = None
        self.reserve = None
        self.orchestrator = None
        self._apply_in_progress = False
        self._apply_pause_until = 0.0
        # Сериализует доступ к tester-селектору (self._top) и socks между плановым
        # прогоном (лёгкая/тяжёлая фазы) и внеплановым зондом монитора.
        self._tester_lock = threading.Lock()
        # Ноды последнего перечисления и анти-ТСПУ зазоры: их читает и процесс резерва,
        # который может начать работу до первого прохода.
        self._node_by_raw: dict = {}
        self._endpoints: dict = {}
        self._host_ep_last: dict = {}
        self._released_at: dict = {}            # crc → когда снята для резерва (monotonic)
        self._phase_tests: dict = {}            # имя → инстанс теста резерва (обязательные, heavy)

        # Админка (Фаза 2-3): живой лог, внеплановый прогон, статус.
        self.log = LogRing()
        self._pass_requested = threading.Event()
        self._request_lock = threading.Lock()
        self._progress = {"phase": "stopped", "processed": 0, "total": 0, "node": None}
        self._last_pass_result = None
        self._wait_until = None
        self._current_pass = 0
        # Прогон разбужен сроком ротации (rotation_bound) → тестируем только ноды групп,
        # у которых ротация наступила. Первый и внеплановый прогоны — полные.
        self._scoped_next = False
        # Прогон прерван применением конфига (конвейер перезапускает sing-box, состав нод
        # меняется) — следующий начнётся сразу после окна применения, без ожидания ротации.
        self._pass_aborted = False
        # Сквозной номер прогона (meta.pass_seq): НЕ сбрасывается в полночь, в отличие от
        # посуточного pass_no. По нему считается пауза (backoff) нод в прогонах.
        self._pass_seq = 0
        self._current_day = ""
        self._running = False
        self._httpd = None
        self._banned: set = set()
        self.notifier = Notifier(cfg.notify,
                                 fallback=self.via_tester if cfg.notify.via_tester else None)
        self.singbox = SingboxControl(cfg.singbox_control, self.notifier)

        # Storage создаём РАНЬШЕ switcher/scoreboard: рейтинг и состояние переключений
        # живут в БД.
        self.storage = None
        self.collector = None
        if cfg.storage.enabled:
            self.storage = Storage(cfg.storage)
            if cfg.storage.traffic.enabled:
                self.collector = TrafficCollector(
                    cfg.storage.traffic, self.api, self.storage)

        if cfg.scoring.enabled and self.storage is not None:
            self.board = Scoreboard(self.storage, cfg.scoring)
            if cfg.switching.enabled:
                traffic_provider = None
                lb = cfg.switching.rotation.load_balance
                if lb.enabled:
                    traffic_provider = _traffic_provider_factory(self.storage, lb.window_hours * 3600.0)
                self.switcher = Switcher(cfg.switching, self.api, self.board,
                                         self._top, traffic_provider, self.storage)
                self.switcher.on_activation = self._on_activation
                self.reserve = ReserveKeeper(self)
                if cfg.monitor.enabled:
                    # Монитору нужен боевой трафик по нодам — поток соединений держим
                    # и без записи в БД (storage.traffic выключен).
                    if self.collector is None:
                        self.collector = TrafficCollector(cfg.storage.traffic, self.api)
                    self.monitor = ProductionMonitor(
                        cfg.monitor, self.api, self.switcher,
                        prober=self._probe_node, traffic=self.collector.user_bytes,
                        pause_check=self._apply_paused,
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
        for leaf in self.api.list_group_members(top):
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

    def _groups_params(self, groups) -> "RunParams":
        key = tuple(groups)
        if key not in self._region_cache:
            self._region_cache[key] = self.cfg.run.for_node_groups(self._base, key)
        return self._region_cache[key]

    def _node_params(self, ident: NodeIdentity):
        """Параметры тестов ноды — из всех её групп переключения."""
        groups = self.board.node_groups(ident.raw) if self.board is not None else []
        return self._groups_params(groups)

    def _refresh_groups(self) -> None:
        """Состав групп переключения из sing-box: selector {name}-auto-out с членом
        {name}-auto-out-failsafe; члены — leaf-ноды (с CRC)."""
        if self.board is None:
            return
        try:
            proxies = self.api.all_proxies()
        except ApiError as exc:
            print(f"  [!] состав групп не получен: {exc}")
            return
        members = {}
        for tag, info in proxies.items():
            if not tag.endswith("-auto-out") or f"{tag}-failsafe" not in (info.get("all") or []):
                continue
            leaves = {m for m in info["all"] if parse_node(m).node_id}
            if leaves:
                members[tag[:-len("-auto-out")]] = leaves
        self.board.set_groups(members)
        if members and self.switcher is not None:
            self.switcher.prune_groups(set(members))
        required, strict = {}, set()
        for group in self.board.regions():
            params = self._groups_params([group])
            required[group] = {t: self._required_ttl(t) for t in params.required_tests}
            if params.strict_exit_ip:
                strict.add(group)
        self.board.set_required(required)
        self.board.set_strict(strict)
        if self.storage is not None:
            self.board.set_ip_info(self.storage.load_ip_info())

    def _required_ttl(self, test: str) -> float:
        """Срок годности результата обязательного теста (tests.<name>.ttl_hours, деф. 24 ч)."""
        return float((self.cfg.tests.get(test) or {}).get("ttl_hours", 24)) * 3600

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
            self.api.ping()
            if not self._base.tests_enabled:
                raise ValueError("run.default.tests_enabled пуст — нечего тестировать")

            if self.board is not None:
                self.board.set_heavy_ttl(self._base.heavy_ttl_hours * 3600)

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
            if self.reserve is not None:
                self.reserve.start()
            if self.collector is not None:
                self.collector.start()
            self._start_orchestrator()
            self.singbox.start_watch()
            if self.cfg.dashboard.enabled:
                self._start_dashboard()
            while True:
                self._wait_apply_window()
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
                except ApiError as exc:
                    print(f"[!] Прогон #{pass_no} прерван ошибкой API sing-box: {exc}")
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
                if self._pass_aborted:
                    continue                           # ждём окно применения и повторяем
                if empty:
                    self._wait_interruptible(_EMPTY_PASS_RETRY)   # пауза пустого прохода
                else:
                    self._wait_before_next_pass()
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
        finally:
            self._running = False
            self.singbox.stop_watch()
            self._progress = {**self._progress, "phase": "stopped", "node": None}
            self._wait_until = None
            if self.monitor is not None:
                self.monitor.stop()
            if self.reserve is not None:
                self.reserve.stop()
            if self.collector is not None:
                self.collector.stop()
            self._stop_dashboard()
            if self.orchestrator is not None:
                self.orchestrator.stop()
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
            self._scoped_next = False           # запрос из админки — полный прогон
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
            "apply_paused": self._apply_paused(),
        }
        if self.switcher is not None:
            for region in self.switcher.active_regions():
                out["regions"].append({
                    "region": region,
                    "active": self.switcher.active_node(region),
                })
            out["next_rotation"] = self.switcher.next_rotate_deadline()
        return out

    def _apply_start(self) -> None:
        self._apply_in_progress = True

    def _apply_end(self) -> None:
        self._apply_in_progress = False
        self._apply_pause_until = time.monotonic() + 120

    def _apply_paused(self) -> bool:
        return self._apply_in_progress or time.monotonic() < self._apply_pause_until

    def _wait_apply_window(self) -> None:
        while self._apply_paused():
            self._progress = {**self._progress, "phase": "apply_wait", "node": None}
            time.sleep(1)

    def _start_orchestrator(self) -> None:
        if not self.cfg.dashboard.token:
            return
        from pathlib import Path
        schedule = Path(self.cfg.path).parent / "pipeline.json"
        if not schedule.is_file():
            return
        from nodes_admin.orchestrator import Orchestrator
        try:
            self.orchestrator = Orchestrator(
                schedule.parent, Path.cwd(),
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
        sets = {"router": "main", "clients": "wh"}
        if row.get("dry_run") or row.get("mode") not in sets or self.orchestrator is None:
            return
        name = sets[row["mode"]]
        try:
            with open(self.orchestrator.data_dir / "raw" / f"{name}.json", encoding="utf-8") as fh:
                sources = json.load(fh).get("sources") or []
        except (OSError, ValueError, AttributeError):
            return
        stale_max = 48.0
        try:
            with open(self.orchestrator.config_dir / f"config-{name}" / "providers.json",
                      encoding="utf-8") as fh:
                stale_max = float((json.load(fh).get("fetch") or {}).get("stale_max_hours", 48))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.notifier.subscriptions_state(sources, stale_max)

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
        # Без дедлайнов — один интервал от начала ожидания (не пересчитывать каждый шаг,
        # иначе ожидание не кончится); не меньше минуты, чтобы не крутить прогоны.
        fallback = time.time() + max(float(self.cfg.switching.rotation.interval), 60.0)
        announced = None                 # срок, о котором уже сообщили в лог
        while True:
            target = self.switcher.next_rotate_deadline()
            now = time.time()
            due = target if target is not None else fallback
            wait = due - now
            if wait <= 0:
                print("\n===== rotation_bound: настал срок ротации → новый прогон =====")
                self._scoped_next = True
                return
            # Сообщаем при старте и при сдвиге срока, а не на каждом шаге опроса.
            if announced is None or abs(due - announced) > poll:
                mins = wait / 60.0
                tgt = ("ближайшая ротация" if target is not None
                       else "регионы не активированы, повтор")
                print(f"\n===== rotation_bound: ждём {mins:.1f} мин ({tgt}) =====")
                announced = due
            if self._wait_interruptible(min(wait, poll)):
                print("\n===== внеплановый прогон (запрос из админки) =====")
                return

    def _run_pass(self, pass_no: int) -> bool:
        """Один полный прогон. Список нод перечитывается из API sing-box.
        Возвращает True, если прогон пустой (нод нет) — вызывающий выдержит retry-паузу,
        чтобы не крутить цикл вплотную."""
        # Снимок состояния хранилища ДО перечисления: бан влияет на список нод (skip),
        # backoff — на пропуск в обходе, endpoints — на host-aware раскладку.
        self._backoff = {}
        self._progress = {"phase": "enumerating", "processed": 0, "total": 0, "node": None}
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

        scoped, self._scoped_next = self._scoped_next, False
        self._pass_aborted = False
        if self.board is not None:
            self.board.set_restricted(self._restricted_now() | self._banned)
        nodes = self._enumerate_nodes()
        self._refresh_groups()
        if self.storage is not None:
            # Физическое присутствие ведём по ВСЕМ leaf-членам selector, включая бан и
            # исключённые регионы; test-фильтры не должны превращать ноду в «удалённую».
            self.storage.touch_seen(self._present_crcs)
            self.storage.reconcile_presence(self._present_crcs)
        if not nodes:
            print(f"[!] Прогон #{pass_no}: в группе '{self._top}' нет тестируемых нод — пропуск")
            return True
        # node_by_raw — все ноды: обязательные тесты (gemini) живут по своему сроку
        # годности и не должны зависеть от того, чья ротация разбудила прогон.
        node_by_raw = {ident.raw: (region, ident) for region, ident in nodes}
        self._node_by_raw = node_by_raw
        if self.reserve is not None:
            self.reserve.wake()               # состав нод известен — резерв можно собирать
        due = self._due_groups() if scoped else None
        if due:
            total = len(nodes)
            nodes = [(r, i) for r, i in nodes if self._in_due_groups(i.raw, due)]
            print(f"\n===== Прогон #{pass_no}: ротация {', '.join(sorted(due))} — "
                  f"нод {len(nodes)} из {total} =====")
        else:
            print(f"\n===== Прогон #{pass_no}: нод {len(nodes)} =====")

        self._host_ep_last = {}               # host -> {sig: monotonic} (зазор, все тесты)
        if self.board is not None:
            self.board.set_pass(pass_no)
            self.board.begin_pass()
            if due:
                tested = {i.raw for _r, i in nodes}
                for raw in node_by_raw:        # не тестируем — но строку и score сохраняем
                    if raw not in tested:
                        self.board.keep(raw)

        exit_ips = []
        for index, (region, ident) in enumerate(self._order_by_host(nodes)):
            self._progress = {"phase": "testing", "processed": index,
                              "total": len(nodes), "node": ident.raw}
            if self._apply_paused():
                return self._abort_pass(scoped, node_by_raw)
            params = self._node_params(ident)
            if self._backed_off(ident.node_id):     # backoff/карантин — не тестируем
                if self.board is not None:
                    self.board.keep(ident.raw)   # чтобы end_pass не удалил строку/score
                continue
            self._respect_host_gap(self._host_of(ident), self._ep_sig(ident), params.min_host_gap)
            record = self._test_node(region, ident, pass_no,
                                     self._tests_for(params.tests_enabled), params)
            if self._apply_paused():                # замер мог попасть на перезапуск sing-box
                return self._abort_pass(scoped, node_by_raw)
            if record is None:                      # ноды уже нет в селекторе
                if self.board is not None:
                    self.board.keep(ident.raw)
                continue
            if self.storage is not None and self.cfg.storage.store_results:
                self.storage.add_results(record)
            exit_ips.append((record.get("tests") or {}).get("connectivity", {}).get("exit_ip"))
            gate = self._score_and_maybe_switch(region, ident, record)
            self._backoff_update(ident.node_id, gate, low=gate and self._low_score(ident.raw))

        self._refresh_ip_info(exit_ips)

        if self.board is not None:
            self.board.end_pass(pass_no)
        if self.switcher is not None:
            self._progress = {**self._progress, "phase": "switching", "node": None}
            # Частичный прогон: оцениваем только группы, чьи ноды проверены (у прочих
            # рейтинг не обновлялся — quality-счётчик не должен копиться на старых данных).
            groups = None
            if due:
                groups = set(due)
                for _r, i in nodes:
                    groups.update(self.board.node_groups(i.raw))
            self.switcher.evaluate_all(groups)
        if self.reserve is not None:
            self.reserve.wake(ratings_changed=True)
        self._progress = {**self._progress, "processed": self._progress["total"], "node": None}
        return False

    def _abort_pass(self, scoped: bool, node_by_raw: dict) -> bool:
        """Конвейер начал применять конфиг: остаток прогона по старому списку нод не
        имеет смысла, а замеры во время перезапуска sing-box ложно проваливают gate.
        Рейтинг сохраняем как есть, переключение не оцениваем; прогон повторится после
        окна применения (с тем же охватом)."""
        print("  · конвейер применяет конфиг — прогон прерван, повтор после применения")
        if self.board is not None:
            for raw in node_by_raw:
                self.board.keep(raw)
            self.board.end_pass(self._current_pass)
        self._scoped_next = scoped
        self._pass_aborted = True
        return False

    def _refresh_ip_info(self, ips) -> None:
        """Тип выходного IP для новых/устаревших exit_ip прогона (один пакетный запрос)."""
        cfg = self.cfg.ip_info
        if not cfg.enabled or self.storage is None:
            return
        stale = self.storage.ip_info_stale(ips, cfg.ttl_days * 86400)
        if not stale:
            return
        from .ipinfo import lookup
        infos = {}
        if cfg.via_tester:
            # Через SOCKS тестера (socks5h: имя резолвит нода): DNS-фильтр роутера часто
            # блокирует ip-api.com.
            infos = self.via_tester(lambda session: lookup(stale, cfg.url, cfg.timeout,
                                                           session=session)) or {}
        if not infos:
            infos = lookup(stale, cfg.url, cfg.timeout)
        self.storage.save_ip_info(infos)
        if self.board is not None:
            self.board.set_ip_info(self.storage.load_ip_info())
        print(f"  · тип выходного IP: обновлено {len(infos)} из {len(stale)}")

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
                session = make_session(self.cfg.testing_group.connection)
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
        if self.board is None:
            return []
        seen, out = set(), []
        for group in self.board.regions():
            for cand in self.board.candidates(group):
                if cand["node"] not in seen:
                    seen.add(cand["node"])
                    out.append(cand)
        out.sort(key=lambda c: float(c.get("score") or 0), reverse=True)
        return [c["node"] for c in out[:limit]]

    def _due_groups(self) -> set:
        """Группы, у которых в этом прогоне наступает ротация (или нет активной), и
        группы в аварии без замены — их ноды нужно перепроверить."""
        if self.board is None or self.switcher is None:
            return set()
        return {g for g in self.board.regions()
                if self.switcher.rotation_due(g) or self.switcher.stuck(g)}

    def _in_due_groups(self, raw: str, due: set) -> bool:
        """Нода входит в группу с наступившей ротацией. Нода вне групп (новая, без
        состава) тестируется всегда."""
        groups = self.board.node_groups(raw)
        return not groups or bool(due.intersection(groups))

    # --- Проверки для резерва (nodes_tester.reserve) ---------------------

    def _on_activation(self, group: str, reason: str, node: str, prev) -> None:
        """Смена активной (или уход на failsafe): уведомление и добор резерва."""
        self.notifier.activation(group, reason, node, prev)
        if self.reserve is not None:
            self.reserve.wake()

    def reserve_size(self, group: str) -> int:
        return max(0, int(self._groups_params([group]).reserve_size or 0))

    def _phase_test(self, name: str):
        """Инстанс теста по имени (кэш); None — тест не зарегистрирован."""
        if name not in self._phase_tests:
            cls = get_test_class(name)
            self._phase_tests[name] = cls(self.cfg.tests.get(name) or {}) if cls else None
        return self._phase_tests[name]

    def check_node(self, raw: str, check: str) -> None:
        """Обязательный тест группы (gemini, openai, anthropic) или "heavy" на одной ноде;
        вердикт — в рейтинг, замер — в results. Сетевой сбой обязательного теста
        (inconclusive) прошлый вердикт не отменяет."""
        node = self._node_by_raw.get(raw)
        test = self._phase_test("heavy_download" if check == "heavy" else check)
        if node is None or test is None:
            return
        region, ident = node
        res = self._run_one(test, region, ident)
        if res is None:
            return
        ok = bool(res.get("ok"))
        if check == "heavy":
            self.board.set_heavy(raw, ok)
            verdict = "OK" if ok else "VETO"
            detail = f"{res.get('speed_mbps', '-')}Mbps"
        else:
            if not res.get("inconclusive"):
                self.board.set_required_result(raw, check, ok, res.get("country") or "")
            verdict = "OK" if ok else ("INCONCLUSIVE" if res.get("inconclusive") else "FAIL")
            detail = f"{res.get('countries') or res.get('verdicts') or ''} {res.get('error') or ''}".strip()
        if self.storage is not None and self.cfg.storage.store_results:
            self.storage.add_results({"round": self._current_pass, "id": ident.node_id,
                                      "node": raw, "tests": {test.name: res}})
        if self.cfg.report.console:
            print(f"  [reserve] {ident.short()}: {test.name}={verdict} {detail}".rstrip())

    def release_for_reserve(self, group: str, skip: set) -> "str | None":
        """Резерву группы не хватает кандидатов: снять паузу или карантин с ноды группы,
        которая ограничена дольше всех, и сразу проверить её лёгкими тестами. Провал
        вернёт её в карантин с прежним счётчиком. Бан не снимается; нода, снятая и
        провалившаяся недавно (_RELEASE_RETRY), повторно не снимается.
        Возвращает снятую ноду (годится ли она — решит рейтинг) или None — снимать некого."""
        if not self.cfg.cooldown.enabled or self.storage is None or self.board is None:
            return None
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
        _until, _until_pass, streak, reason = backoff[crc]
        # Запись остаётся с прежним streak: провал gate вернёт ноду в карантин.
        if reason == "garbage":
            self.storage.set_backoff(crc, int(time.time()), streak, reason)
            self._backoff[crc] = (int(time.time()), 0, streak, reason)
        else:
            self.storage.set_backoff(crc, None, streak, reason, until_pass=0)
            self._backoff[crc] = (0, 0, streak, reason)
        self.board.restrict(crc, False)
        self.storage.add_node_event(crc, "restriction_cleared", "reserve", streak)
        self._released_at[crc] = now
        print(f"  [reserve] {group}: кандидатов не хватает — снято ограничение с {ident.short()}")
        params = self._node_params(ident)
        record = self._test_node(region, ident, self._current_pass,
                                 self._tests_for(params.tests_enabled), params)
        if record is None:
            return raw
        if self.storage is not None and self.cfg.storage.store_results:
            self.storage.add_results(record)
        gate = self._score_and_maybe_switch(region, ident, record)
        self._backoff_update(crc, gate, low=gate and self._low_score(raw))
        return raw

    def _run_one(self, test, region: str, ident: NodeIdentity) -> "dict | None":
        """Один тест (обязательный, тяжёлый) на ноде через тестовый селектор.
        None — переключиться на ноду не удалось. Лок общий с зондом монитора;
        host-gap-пауза — до лока, запись в БД — у вызывающего, вне лока."""
        params = self._node_params(ident)
        self._respect_host_gap(self._host_of(ident), self._ep_sig(ident), params.min_host_gap)
        with self._tester_lock:
            self._remember(self._top)
            try:
                self.api.select(self._top, ident.raw)
            except ApiError as exc:
                print(f"  {ident.short()}: ОШИБКА переключения ({test.name}): {exc}")
                return None
            if params.switch_delay > 0:
                time.sleep(params.switch_delay)
            session = make_session(self.cfg.testing_group.connection)
            ctx = TestContext(session=session, node=ident.raw,
                              default_timeout=params.request_timeout, region=region)
            try:
                return test.run(ctx).to_dict()
            except Exception as exc:  # noqa: BLE001 — сбой теста не рушит проход
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                session.close()

    def _score_and_maybe_switch(self, region: str, ident, record) -> bool:
        if self.board is None:
            return True                      # без борда gate неизвестен — не куладаунить
        _final, _s_run, gate = self.board.record(ident, region, record.get("tests", {}))
        if self.switcher is not None:
            self.switcher.notify_score(region, ident.raw, blocked=not gate)
        return gate

    # --- Пауза (в прогонах) / карантин (во времени) по провалу gate, персистентно ---

    def _in_effect(self, entry: tuple, now: float) -> bool:
        """Запись backoff (until, until_pass, streak, reason) ещё действует: карантин —
        по времени, пауза — по сквозному номеру прогона."""
        until, until_pass, _streak, reason = entry
        if reason == "garbage":
            return now < until
        return self._pass_seq <= until_pass

    def _backed_off(self, crc: str) -> bool:
        if not self.cfg.cooldown.enabled or not crc:
            return False
        entry = self._backoff.get(crc)
        return bool(entry) and self._in_effect(entry, time.time())

    def restricted_crcs(self) -> set:
        """CRC с действующей паузой/карантином по свежему состоянию БД (для админки).
        Истёкшая, но ещё не снятая запись garbage ограничением не считается."""
        if not self.cfg.cooldown.enabled or self.storage is None:
            return set()
        return self._restricted_now(self.storage.load_backoff())

    def _restricted_now(self, backoff: "dict | None" = None) -> set:
        """CRC с действующей паузой/карантином по снимку backoff (по умолчанию — прогона)."""
        if not self.cfg.cooldown.enabled:
            return set()
        if backoff is None:
            backoff = self._backoff
        now = time.time()
        return {crc for crc, entry in backoff.items() if self._in_effect(entry, now)}

    def restrict_node(self, crc: str, on: bool, entry: "tuple | None" = None) -> None:
        """Ручной карантин/бан (on) или снятие (off) из админки: сразу убрать ноду из
        кандидатов переключателя и, если она активна в какой-то группе, заменить её."""
        if on and entry is not None:
            self._backoff[crc] = entry          # прогон в процессе тоже её пропустит
        elif not on:
            self._backoff.pop(crc, None)
        if self.board is None:
            return
        self.board.restrict(crc, on)
        if on and self.switcher is not None:
            for group in self.switcher.active_regions():
                active = self.switcher.active_node(group)
                if active and parse_node(active).node_id == crc:
                    self.switcher.evaluate_region(group, emergency=True)

    def _low_score(self, raw: str) -> bool:
        """Живая, но слабая нода: рейтинг ниже cooldown.low_score (вкл. при > 0).
        Активные и новые (< _LOW_SCORE_MIN_SAMPLES замеров) не трогаем."""
        threshold = float(self.cfg.cooldown.low_score or 0)
        row = self.board.get(raw) if threshold > 0 and self.board is not None else None
        if not row or int(row.get("active") or 0):
            return False
        return (int(row.get("samples") or 0) >= _LOW_SCORE_MIN_SAMPLES
                and float(row.get("score") or 0) < threshold)

    def _backoff_update(self, crc: str, gate: bool, low: bool = False) -> None:
        # Backoff персистентен → нужен storage. Без него ноды тестируются каждый проход.
        if not self.cfg.cooldown.enabled or not crc or self.storage is None:
            return
        prev = self._backoff.get(crc)       # (until, until_pass, streak, reason) до обновления
        if low:                             # gate пройден, но рейтинг ниже порога → карантин
            cd = self.cfg.cooldown
            streak = (prev[2] if prev else 0) + 1
            until = int(time.time() + cd.garbage_hours * 3600.0)
            self.storage.set_backoff(crc, until, streak, "garbage")
            if prev is None or prev[3] != "garbage":
                self.storage.add_node_event(crc, "garbage", "low-score", streak)
            self._backoff[crc] = (until, 0, streak, "garbage")
            if self.board is not None:
                self.board.restrict(crc, True)
            print(f"  · рейтинг ниже {cd.low_score:g}: {crc} → карантин на {cd.garbage_hours:g} ч")
            return
        if gate:
            if crc in self._backoff:
                self.storage.clear_backoff(crc)      # нода жива — снять backoff/карантин
                self._backoff.pop(crc, None)
                if self.board is not None:
                    self.board.restrict(crc, False)
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
        if self.board is not None:
            self.board.restrict(crc, True)
        if garbage:
            print(f"  · gate-провал {crc} → карантин на {cd.garbage_hours:g} ч")
        else:
            print(f"  · gate-провал {crc} → пауза #{streak}: пропуск {skip} прогон(ов)")

    def _probe_node(self, region: str, leaf: str) -> tuple[bool, float, bool, str]:
        """Внеплановый зонд активной ноды для монитора: закачка ~probe_bytes через socks.

        Возвращает (ok, mbps, inconclusive, error). inconclusive — туннель жив, но замер не
        показателен (429/limited, http-ошибка): монитор трактует как «не throttle», без
        страйка. ApiError (переключение/API) пробрасывает наверх — монитор пропустит.
        """
        mon = self.cfg.monitor
        url = mon.probe_url or f"https://speed.cloudflare.com/__down?bytes={mon.probe_bytes}"
        with self._tester_lock:
            self._remember(self._top)
            self.api.select(self._top, leaf)     # ApiError → наверх (монитор пропустит)
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
        return ok, mbps, inconclusive, str(res.get("error") or "")

    def _remember(self, tag: str) -> None:
        if not self._base.restore_selection or tag in self._originals:
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

    def _test_node(self, region: str, ident: NodeIdentity, pass_no: int, tests,
                   params) -> "dict | None":
        """Результат проверки ноды; None — ноды уже нет в селекторе (конфиг sing-box
        сменился после перечисления), оценивать нечего."""
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
                self.api.select(self._top, leaf)
            except ApiError as exc:
                if "not found in selector" in str(exc):
                    print(f"  {ident.short()}: ноды нет в селекторе (конфиг сменился) — пропуск")
                    return None
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
