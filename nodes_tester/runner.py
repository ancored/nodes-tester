"""Оркестратор прогона.

Определение региона ноды — по region_groups.recognition:
  by_selector — nodes-tester содержит РЕГИОНАЛЬНЫЕ под-селекторы (двухуровнево):
                PUT nodes-tester = <регион>-nodes-tester; PUT <регион> = <leaf>.
  parse       — nodes-tester ПЛОСКИЙ (все leaf), регион из страны в имени ноды:
                PUT nodes-tester = <leaf>.
  manually    — плоский nodes-tester, регион по спискам из конфига.

Порядок теста: группируем leaf по (регион · протокол · провайдер), между группами
пауза group_pause. Параметры прогона послойные: default → testing_group → регион.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .clash_api import ClashApiClient, ClashApiError
from .config import Config
from .identity import NodeIdentity, coarse_region, parse_node, region_label
from .monitor import ProductionMonitor
from .proxy import make_session
from .reporter import Reporter, run_timestamp
from .scoreboard import Scoreboard
from .storage import Storage
from .switcher import Switcher
from .tests import TestContext, get_test_class
from .traffic import TrafficCollector


# Как часто гонять единый cleanup() БД в непрерывном режиме (VACUUM — отдельно, cron).
_CLEANUP_INTERVAL = 24 * 3600


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


@dataclass
class TestGroup:
    region: str                       # коарс-регион (eu/us/ru/other)
    regional_tag: Optional[str]       # тег под-селектора (by_selector) или None (flat)
    protocol: str
    provider: str
    nodes: list[NodeIdentity] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.region.upper()} · {self.protocol} · {self.provider}"


class Runner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.clash = ClashApiClient(cfg.clash_api)
        self._top = cfg.testing_group.selector.group
        self.board = None
        self.switcher = None
        self.monitor = None

        # Storage создаём РАНЬШЕ switcher: балансировке ротации нужен трафик из БД.
        self.storage = None
        self.collector = None
        if cfg.storage.enabled:
            self.storage = Storage(cfg.storage)
            if cfg.storage.traffic.enabled:
                self.collector = TrafficCollector(
                    cfg.storage.traffic, self.clash, self.storage)

        if cfg.scoring.enabled:
            self.board = Scoreboard(cfg.scoring.file, cfg.scoring)
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
                    self.monitor = ProductionMonitor(cfg.monitor, self.clash, self.switcher)

    # --- Планирование --------------------------------------------------

    def _enumerate_leaves(self):
        """(region, regional_tag|None, NodeIdentity) для всех тестируемых нод."""
        top = self._top
        exclude = set(self.cfg.testing_group.selector.exclude) | {top}
        rg = self.cfg.region_groups
        excl_regions = {r.lower() for r in rg.exclude}

        if rg.recognition == "by_selector":
            regionals = [m for m in self.clash.list_group_members(top) if m not in exclude]
            for regional_tag in regionals:
                region = region_label(regional_tag, top)
                if region.lower() in excl_regions:
                    continue
                for leaf in self.clash.list_group_members(regional_tag):
                    if leaf not in exclude:
                        yield region, regional_tag, parse_node(leaf)
        else:
            manual = rg.manual_map() if rg.recognition == "manually" else {}
            for leaf in self.clash.list_group_members(top):
                if leaf in exclude:
                    continue
                ident = parse_node(leaf)
                region = (coarse_region(ident.country) if rg.recognition == "parse"
                          else manual.get(leaf, "other"))
                if region.lower() not in excl_regions:
                    yield region, None, ident

    def _build_plan(self) -> list[TestGroup]:
        order: list[tuple] = []
        buckets: dict[tuple, TestGroup] = {}
        for region, regional_tag, ident in self._enumerate_leaves():
            key = (region, regional_tag, ident.protocol, ident.provider)
            if key not in buckets:
                buckets[key] = TestGroup(region=region, regional_tag=regional_tag,
                                         protocol=ident.protocol, provider=ident.provider)
                order.append(key)
            buckets[key].nodes.append(ident)
        return [buckets[k] for k in order]

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
        self._cooldown: dict[str, dict] = {}   # crc -> {fail_streak, resume_at} (backoff)
        self._garbage: dict = {}
        self._garbage_trial: set = set()

        if not self._base.tests_enabled:
            raise ValueError("run.default.tests_enabled пуст — нечего тестировать")

        if self.board is not None:             # TTL heavy-veto (двухуровневое тестирование)
            self.board.set_heavy_veto_ttl(self._base.heavy_veto_hours * 3600)

        reporter = Reporter(self.cfg.report, run_timestamp()) if self.cfg.report.enabled else None

        if not self._base.loop:
            mode = f"прогонов: {self._base.rounds}"
        elif self._rotation_bound_active():
            mode = "rotation_bound (прогон к сроку ротации), Ctrl+C для остановки"
        else:
            mode = "непрерывно, Ctrl+C для остановки"
        print(f"Группа '{self._top}' (recognition={self.cfg.region_groups.recognition}); "
              f"тесты: {', '.join(self._base.tests_enabled)}; {mode}")
        print(f"Результаты: {reporter.path if reporter else 'только в SQLite'}")
        if self.monitor is not None:
            print(f"Монитор активных нод: каждые {self.cfg.monitor.interval}s")
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
                try:
                    self._run_pass(pass_no, reporter)
                except ClashApiError as exc:
                    print(f"[!] Прогон #{pass_no} прерван ошибкой Clash API: {exc}")
                if self.storage is not None:
                    self.storage.set_meta("pass_day", current_day)
                    self.storage.set_meta("pass_no", pass_no)     # посуточный номер
                if (self.storage is not None
                        and time.monotonic() - self._last_cleanup >= _CLEANUP_INTERVAL):
                    self.storage.cleanup()             # единая очистка БД (без VACUUM)
                    self._last_cleanup = time.monotonic()
                if not self._should_continue(pass_no):
                    break
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
            if reporter is not None:
                reporter.close()
            self._restore()

        return reporter.path if reporter else ""

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

    def _run_pass(self, pass_no: int, reporter: Optional[Reporter]) -> None:
        """Один полный прогон. Список нод перечитывается из Clash API."""
        plan = self._build_plan()
        if not plan:
            print(f"[!] Прогон #{pass_no}: в группе '{self._top}' нет нод — пропуск")
            return

        total = sum(len(g.nodes) for g in plan)
        print(f"\n===== Прогон #{pass_no}: групп {len(plan)}, нод {total} =====")

        # Снимок карантина на начало прогона: active — пропускаем целиком; trial —
        # карантин истёк, даём одну пробу (провал gate → сразу назад в карантин).
        self._garbage: dict = {}
        self._garbage_trial: set = set()
        if self.storage is not None:
            self.storage.maybe_load_nodes(self.cfg.storage.nodes_file)
            gmap = self.storage.all_garbage()
            now = time.time()
            self._garbage = {c: u for c, u in gmap.items() if u > now}
            self._garbage_trial = {c for c, u in gmap.items() if u <= now}
        if self.board is not None:
            self.board.set_pass(pass_no)
            self.board.begin_pass()

        current_region_tag = None
        first_group = True
        for group in plan:
            params = self._region_params(group.region)
            if not first_group and params.group_pause > 0:
                print(f"  … пауза между группами {params.group_pause}s")
                time.sleep(params.group_pause)
            first_group = False

            # by_selector: переключаем верхний селектор на регион (при смене).
            if group.regional_tag is not None and group.regional_tag != current_region_tag:
                self._remember(self._top)
                self._remember(group.regional_tag)
                self.clash.select(self._top, group.regional_tag)
                current_region_tag = group.regional_tag

            tests = self._tests_for(params.tests_enabled)
            print(f"\n── {group.title} — нод: {len(group.nodes)} ──")
            for ident in group.nodes:
                if ident.node_id in self._garbage:      # карантин (мусорная нода)
                    if self.board is not None:
                        self.board.keep(ident.raw)
                    continue
                if self._cooldown_skip(ident.node_id, pass_no):
                    if self.board is not None:
                        self.board.keep(ident.raw)   # чтобы end_pass не удалил ноду
                    print(f"  · {ident.short()} — cooldown, пропуск")
                    continue
                record = self._test_node(group, ident, pass_no, tests, params)
                if reporter is not None:
                    reporter.add(record)
                if self.storage is not None and self.cfg.storage.store_results:
                    self.storage.add_results(record)
                gate = self._score_and_maybe_switch(group, ident, record)
                self._cooldown_update(ident.node_id, pass_no, gate)

        # Фаза 2 (двухуровневое): тяжёлый 50МБ download-veto только для кандидатов.
        self._run_heavy_pass(plan, pass_no, reporter)

        if reporter is not None:
            reporter.flush()
        if self.board is not None:
            self.board.end_pass(pass_no)
        if self.switcher is not None:
            self.switcher.evaluate_all()

    def _heavy_test(self):
        """Инстанс тяжёлого download-теста (кэш). None — если тест не зарегистрирован."""
        if not hasattr(self, "_heavy_cache"):
            cls = get_test_class("heavy_download")
            self._heavy_cache = cls(self.cfg.tests.get("heavy_download") or {}) if cls else None
        return self._heavy_cache

    def _heavy_targets(self, plan) -> list:
        """Кандидаты на тяжёлый download: топ-heavy_candidates по (лёгкому) score на
        регион + активная нода. Возвращает [(group, ident), …] без повторов, по регионам."""
        node_by_raw = {ident.raw: (g, ident) for g in plan for ident in g.nodes}
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

    def _run_heavy_pass(self, plan, pass_no: int, reporter: Optional[Reporter]) -> None:
        if self.board is None:
            return
        heavy = self._heavy_test()
        if heavy is None:
            return
        targets = self._heavy_targets(plan)
        if not targets:
            return
        print(f"\n── Фаза 2 · тяжёлый download-veto — нод: {len(targets)} ──")
        current_region_tag = None
        for group, ident in targets:
            if ident.node_id in self._garbage:      # мусорную ноду не качаем
                continue
            params = self._region_params(group.region)
            # by_selector: верхний селектор → регион (при смене), затем регион → leaf.
            if group.regional_tag is not None and group.regional_tag != current_region_tag:
                self._remember(self._top)
                self._remember(group.regional_tag)
                try:
                    self.clash.select(self._top, group.regional_tag)
                except ClashApiError as exc:
                    print(f"  heavy: ОШИБКА выбора региона {group.regional_tag}: {exc}")
                    continue
                current_region_tag = group.regional_tag
            target = group.regional_tag if group.regional_tag is not None else self._top
            self._remember(target)
            try:
                self.clash.select(target, ident.raw)
            except ClashApiError as exc:
                print(f"  {ident.short()}: ОШИБКА переключения (heavy): {exc}")
                continue
            if params.switch_delay > 0:
                time.sleep(params.switch_delay)
            session = make_session(self.cfg.testing_group.connection)
            ctx = TestContext(session=session, node=ident.raw,
                              default_timeout=params.request_timeout, region=group.region)
            res = heavy.run(ctx).to_dict()
            session.close()
            ok = bool(res.get("ok"))
            self.board.set_heavy(ident.raw, ok)     # veto-фильтр (в скоринг НЕ идёт)
            rec = {"round": pass_no, "id": ident.node_id, "node": ident.raw,
                   "tests": {heavy.name: res}}
            if reporter is not None:
                reporter.add(rec)
            if self.storage is not None and self.cfg.storage.store_results:
                self.storage.add_results(rec)
            if self.cfg.report.console:
                print(f"  {ident.short()}: heavy={'OK' if ok else 'VETO'} "
                      f"{res.get('speed_mbps', '-')}Mbps")

    def _score_and_maybe_switch(self, group, ident, record) -> bool:
        if self.board is None:
            return True                      # без борда gate неизвестен — не куладаунить
        _final, _s_run, gate = self.board.record(ident, group.region, record.get("tests", {}))
        if self.switcher is not None:
            self.switcher.notify_score(group.region, ident.raw, blocked=not gate)
        return gate

    # --- Cooldown / экспоненциальный backoff по провалу gate ------------

    def _cooldown_skip(self, crc: str, pass_no: int) -> bool:
        if not self.cfg.cooldown.enabled or not crc:
            return False
        cd = self._cooldown.get(crc)
        return bool(cd and pass_no < cd["resume_at"])

    def _cooldown_update(self, crc: str, pass_no: int, gate: bool) -> None:
        if not self.cfg.cooldown.enabled or not crc:
            return
        if gate:
            self._cooldown.pop(crc, None)    # нода жива — сброс backoff
            if self.storage is not None and crc in self._garbage_trial:
                self.storage.clear_garbage(crc)          # проба после карантина удалась
                self._garbage_trial.discard(crc)
            return
        cd = self._cooldown.get(crc) or {"fail_streak": 0}
        cd["fail_streak"] += 1
        skip = min(2 ** (cd["fail_streak"] - 1), self.cfg.cooldown.max_skip)
        cd["resume_at"] = pass_no + skip + 1
        self._cooldown[crc] = cd
        # Достигли потолка backoff, ИЛИ провалилась проба после карантина → в карантин
        # на garbage_hours (переживает рестарт: больше не теребим мёртвую ноду).
        to_garbage = (self.storage is not None
                      and (skip >= self.cfg.cooldown.max_skip or crc in self._garbage_trial))
        if to_garbage:
            hours = self.cfg.cooldown.garbage_hours
            self.storage.mark_garbage(crc, int(time.time() + hours * 3600))
            self._garbage_trial.discard(crc)
            self._garbage[crc] = int(time.time() + hours * 3600)   # пропуск до конца прогона
            print(f"  · {crc}: gate-провал #{cd['fail_streak']} → карантин {hours}ч (мусорная)")
        else:
            print(f"  · gate-провал #{cd['fail_streak']} ({crc}) → пропуск {skip} прогонов")

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

    def _test_node(self, group: TestGroup, ident: NodeIdentity, pass_no: int, tests, params) -> dict:
        leaf = ident.raw
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": pass_no,
            "provider": ident.provider,
            "protocol": ident.protocol,
            "region": ident.country,          # страна из имени
            "label": ident.label,             # доп. поле из тега (напр. AI)
            "id": ident.node_id,
            "region_group": group.region,     # коарс-регион
            "node": leaf,
        }

        # by_selector: PUT <регион>-nodes-tester = leaf; flat: PUT nodes-tester = leaf.
        target = group.regional_tag if group.regional_tag is not None else self._top
        self._remember(target)
        try:
            self.clash.select(target, leaf)
        except ClashApiError as exc:
            print(f"  {ident.short()}: ОШИБКА переключения: {exc}")
            base["tests"] = {"_select": {"ok": False, "error": str(exc)}}
            return base

        if params.switch_delay > 0:
            time.sleep(params.switch_delay)

        session = make_session(self.cfg.testing_group.connection)
        ctx = TestContext(session=session, node=leaf,
                          default_timeout=params.request_timeout, region=group.region)
        results = {}
        for test in tests:
            if not test.due(pass_no):      # тяжёлые тесты — раз в N прогонов (every)
                continue
            results[test.name] = test.run(ctx).to_dict()
        session.close()

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
