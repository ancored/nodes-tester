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
        if cfg.scoring.enabled:
            self.board = Scoreboard(cfg.scoring.file, cfg.scoring)
            if cfg.switching.enabled:
                self.switcher = Switcher(cfg.switching, self.clash, self.board, self._top)
                if cfg.monitor.enabled:
                    self.monitor = ProductionMonitor(cfg.monitor, self.clash, self.switcher)

        self.storage = None
        self.collector = None
        if cfg.storage.enabled:
            self.storage = Storage(cfg.storage)
            if cfg.storage.traffic.enabled:
                self.collector = TrafficCollector(
                    cfg.storage.traffic, self.clash, self.storage)

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

        if not self._base.tests_enabled:
            raise ValueError("run.default.tests_enabled пуст — нечего тестировать")

        reporter = Reporter(self.cfg.report, run_timestamp()) if self.cfg.report.enabled else None

        mode = ("непрерывно, Ctrl+C для остановки" if self._base.loop
                else f"прогонов: {self._base.rounds}")
        print(f"Группа '{self._top}' (recognition={self.cfg.region_groups.recognition}); "
              f"тесты: {', '.join(self._base.tests_enabled)}; {mode}")
        print(f"Результаты: {reporter.path if reporter else 'только в SQLite'}")
        if self.monitor is not None:
            print(f"Монитор активных нод: каждые {self.cfg.monitor.interval}s")
        if self.storage is not None:
            self.storage.load_nodes(self.cfg.storage.nodes_file)
            print(f"Хранилище: {self.cfg.storage.db_file}"
                  + (" + сбор трафика" if self.collector is not None else ""))

        pass_no = 0
        try:
            if self.monitor is not None:
                self.monitor.start()
            if self.collector is not None:
                self.collector.start()
            while True:
                pass_no += 1
                try:
                    self._run_pass(pass_no, reporter)
                except ClashApiError as exc:
                    print(f"[!] Прогон #{pass_no} прерван ошибкой Clash API: {exc}")
                if not self._should_continue(pass_no):
                    break
                if self._base.pass_pause > 0:
                    print(f"\n===== пауза между прогонами {self._base.pass_pause}s =====")
                    time.sleep(self._base.pass_pause)
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
        finally:
            if self.monitor is not None:
                self.monitor.stop()
            if self.collector is not None:
                self.collector.stop()
            if self.storage is not None:
                self.storage.prune()
                self.storage.close()
            if reporter is not None:
                reporter.close()
            self._restore()

        return reporter.path if reporter else ""

    def _should_continue(self, pass_no: int) -> bool:
        return True if self._base.loop else pass_no < self._base.rounds

    def _run_pass(self, pass_no: int, reporter: Optional[Reporter]) -> None:
        """Один полный прогон. Список нод перечитывается из Clash API."""
        plan = self._build_plan()
        if not plan:
            print(f"[!] Прогон #{pass_no}: в группе '{self._top}' нет нод — пропуск")
            return

        total = sum(len(g.nodes) for g in plan)
        print(f"\n===== Прогон #{pass_no}: групп {len(plan)}, нод {total} =====")

        if self.storage is not None:
            self.storage.maybe_load_nodes(self.cfg.storage.nodes_file)
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
                record = self._test_node(group, ident, pass_no, tests, params)
                if reporter is not None:
                    reporter.add(record)
                if self.storage is not None and self.cfg.storage.store_results:
                    self.storage.add_results(record)
                self._score_and_maybe_switch(group, ident, record)

        if reporter is not None:
            reporter.flush()
        if self.board is not None:
            self.board.end_pass(pass_no)
        if self.switcher is not None:
            self.switcher.evaluate_all()

    def _score_and_maybe_switch(self, group, ident, record) -> None:
        if self.board is None:
            return
        _final, _s_run, gate = self.board.record(ident, group.region, record.get("tests", {}))
        if self.switcher is not None:
            self.switcher.notify_score(group.region, ident.raw, blocked=not gate)

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
        parts.append(f"dl={dl.get('speed_mbps', '-')}Mbps" if dl.get("ok") else "dl=FAIL")
    stab = results.get("stability")
    if stab:
        if stab.get("ok"):
            parts.append(f"hold={stab.get('survive_seconds', '-')}s thr={stab.get('throttle_ratio', '-')}")
        else:
            parts.append(f"hold=FAIL({stab.get('survive_seconds', '-')}s)")
    reach = results.get("reachability")
    if reach:
        parts.append(f"reach={reach.get('reached', '-')}/{reach.get('total', '-')}")
    return "  ".join(parts) if parts else "нет результатов"
