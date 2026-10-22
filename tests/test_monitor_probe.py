"""Зонд монитора: провал — страйк и повтор через retry; всплеск ошибок соединений в логе
sing-box — зонд сразу."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from nodes_tester.config import MonitorConfig
from nodes_tester.monitor import ProductionMonitor
from tests.helpers import make_runner, temp_dir

BREAK = (False, 0.0, False, "ssl_read returned")
GOOD = (True, 50.0, False, "")
LIMITED = (False, 0.0, True, "HTTP 403")


class FakeSwitcher:
    def __init__(self):
        self.emergencies = []
        self.penalized = []

    def penalize(self, node):
        self.penalized.append(node)

    def evaluate_region(self, region, emergency=False):
        self.emergencies.append(region)

    def last_switch(self, region):
        return 0.0


def probe_with(results):
    """Монитор, чей prober отдаёт results по очереди; возвращает (монитор, свитчер, вызовы)."""
    sw = FakeSwitcher()
    calls = []

    def prober(region, node):
        calls.append(node)
        return results.pop(0)

    mon = ProductionMonitor(cfg=MonitorConfig(fails=2), api=None,
                            switcher=sw, prober=prober, traffic=dict)
    return mon, sw, calls


class FailsafeWatchTest(unittest.TestCase):
    """failsafe-out групп без кандидатов наблюдается как нода: тишина — зонд, провалы — direct-out."""

    def setUp(self):
        self.sw = FakeSwitcher()
        self.sw.fallback = {"eu": "failsafe-out", "ai": "block-out"}
        self.sw.fallback_regions = lambda: list(self.sw.fallback.items())
        self.verdicts = []

        def verdict(region, alive):
            self.verdicts.append((region, alive))
            self.sw.fallback[region] = "failsafe-out" if alive else "direct-out"
        self.sw.failsafe_verdict = verdict
        self.results, self.calls = [], []

        def prober(region, node):
            self.calls.append(node)
            return self.results.pop(0)
        self.mon = ProductionMonitor(cfg=MonitorConfig(fails=2, silence=300), api=None,
                                     switcher=self.sw, prober=prober, traffic=dict)
        self.proxies = {"failsafe-out": {"now": "P-vless-out [aaaa0001.00000001]"}}

    def _tick(self, now, traffic=None):
        with contextlib.redirect_stdout(io.StringIO()):
            self.mon._watch_failsafe(self.proxies, traffic or {}, now)

    def test_dead_failsafe_goes_direct_and_returns(self):
        self._tick(0)                                   # начало тишины
        self.results += [BREAK, BREAK]
        self._tick(301)                                 # зонд + повтор: страйк 1
        self.assertEqual(self.calls, ["failsafe-out", "failsafe-out"])
        self.assertEqual(self.verdicts, [])
        self.results += [BREAK, BREAK]
        self._tick(602)                                 # страйк 2 — мёртв
        self.assertEqual(self.verdicts, [("eu", False)])  # block-out группы не трогаем
        self.results += [GOOD]
        self._tick(903)                                 # на direct-out — зонд дальше, ожил
        self.assertEqual(self.verdicts, [("eu", False), ("eu", True)])

    def test_traffic_through_failsafe_leaf_means_alive(self):
        self._tick(0)
        self._tick(301, {"aaaa0001.00000001": 10 ** 9})
        self.assertEqual(self.calls, [])
        self.sw.fallback = {"ai": "block-out"}          # зондировать нечего
        self._tick(10 ** 4)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.mon._failsafe)


class FailsafeVerdictTest(unittest.TestCase):
    """Вердикт зонда меняет запасной член только у нестрогой группы с direct_fallback."""

    def _switcher(self, **group):
        from nodes_tester.config import GroupConfig, SelectionConfig
        from nodes_tester.switcher import Switcher
        selected = []
        api = type("Api", (), {"select": lambda s, sel, member: selected.append((sel, member))})()
        sw = Switcher(SelectionConfig(), api, None, "nodes-tester", [GroupConfig("eu", **group)])
        sw.state = {"eu": {"active": None, "failsafe": True, "fallback": "failsafe-out"}}
        return sw, selected

    def test_direct_and_back(self):
        sw, selected = self._switcher(direct_fallback=True)
        events = []
        sw.on_activation = lambda g, reason, node, prev: events.append((reason, node, prev))
        with contextlib.redirect_stdout(io.StringIO()):
            sw.failsafe_verdict("eu", alive=False)
            sw.failsafe_verdict("eu", alive=False)          # уже на direct — ничего
            sw.failsafe_verdict("eu", alive=True)
        self.assertEqual(selected, [("eu-auto-out", "direct-out"), ("eu-auto-out", "failsafe-out")])
        self.assertEqual(events, [("failsafe", "direct-out", "failsafe-out"),
                                  ("failsafe", "failsafe-out", "direct-out")])

    def test_without_direct_fallback_or_strict_nothing_changes(self):
        for group in ({}, {"strict": True, "direct_fallback": True}):
            with self.subTest(group=group):
                sw, selected = self._switcher(**group)
                sw.failsafe_verdict("eu", alive=False)
                self.assertEqual(selected, [])


class MonitorProbeRetryTest(unittest.TestCase):
    def _probe(self, mon, st, now=0.0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mon._probe_region("us", "node-us", st, now)
        return out.getvalue()

    def test_failed_probe_is_strike_and_retry_after_30s(self):
        mon, sw, calls = probe_with([BREAK])
        st = {"strikes": 0}
        log = self._probe(mon, st, now=100.0)
        self.assertEqual(len(calls), 1)                   # без немедленного повтора
        self.assertEqual((st["strikes"], st["next_probe"]), (1, 130.0))
        self.assertIn("ssl_read returned", log)
        self.assertEqual(sw.emergencies, [])

    def test_emergency_after_fails_failed_probes(self):
        mon, sw, _ = probe_with([BREAK, BREAK])
        st = {"strikes": 0}
        self._probe(mon, st)
        self._probe(mon, st, now=30.0)
        self.assertEqual(sw.emergencies, ["us"])
        self.assertEqual(sw.penalized, ["node-us"])       # хост упавшей — под штраф
        self.assertEqual(st["strikes"], 0)

    def test_good_or_inconclusive_resets_strikes(self):
        for first in (LIMITED, GOOD):
            with self.subTest(first=first):
                mon, sw, calls = probe_with([first])
                st = {"strikes": 1}
                self._probe(mon, st, now=10.0)
                self.assertEqual(st["strikes"], 0)
                self.assertEqual(st["next_probe"], 70.0)  # всплеск ошибок — не раньше 2 × retry


NODE = "P-vless-out [aaaa0001.00000001]"


class FakeApi:
    def all_proxies(self):
        return {NODE: {"type": "vless"},
                "eu-auto-out": {"type": "selector", "now": NODE},
                "global-auto-out": {"type": "selector", "now": "eu-auto-out"}}


class ErrorBurstTest(unittest.TestCase):
    """Ошибки соединений через группу в логе sing-box — зонд, не дожидаясь тишины."""

    def setUp(self):
        self.log = os.path.join(temp_dir(), "sing-box.log")
        open(self.log, "w").close()
        sw = FakeSwitcher()
        sw.active_regions = lambda: ["eu"]
        sw.active_node = lambda region: NODE
        sw.reassert_chain = lambda region, proxies: 0
        sw.fallback_regions = lambda: []
        self.calls = []

        def prober(region, node):
            self.calls.append(region)
            return GOOD
        self.mon = ProductionMonitor(cfg=MonitorConfig(), api=FakeApi(), switcher=sw, prober=prober,
                                     traffic=lambda: {"aaaa0001.00000001": 10 ** 9},
                                     singbox_log=self.log)
        self._tick()                                     # первое чтение — с конца файла

    def _write(self, lines):
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write("".join(f"+0000 2026-10-14 12:12:10 ERROR [1 5.0s] connection: {line}\n" for line in lines))

    def _tick(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.mon._tick()

    def test_burst_through_group_probes_despite_traffic(self):
        self._write(["open connection to 1.2.3.4:443 using outbound/selector[global-auto-out]: "
                     "context deadline exceeded"] * 3
                    + ["open connection to 1.2.3.4:443 using outbound/selector[eu-auto-out]: EOF"] * 2)
        self._tick()
        self.assertEqual(self.calls, ["eu"])

    def test_few_or_unrelated_errors_ignored(self):
        self._write(["open connection to 1.2.3.4:443 using outbound/selector[eu-auto-out]: EOF"] * 4
                    + ["open connection to 1.2.3.4:443 using outbound/selector[nodes-tester]: EOF"] * 5
                    + ["open connection to 1.2.3.4:443 using outbound/selector[eu-auto-out]: "
                       "operation not permitted"] * 5
                    + ["open connection to 1.2.3.4:443 using outbound/direct[direct-out]: i/o timeout"] * 5)
        self._tick()
        self.assertEqual(self.calls, [])

    def test_partial_line_waits_for_newline(self):
        line = "open connection to 1.2.3.4:443 using outbound/selector[eu-auto-out]: EOF"
        self._write([line] * 4)
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write("+0000 x ERROR " + line)                # строка ещё пишется
        self._tick()
        self.assertEqual(self.calls, [])
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write("\n")
        self._tick()                                      # дописанная строка — в новом тике
        self.assertEqual(self.calls, [])                  # 1 ошибка за тик — не всплеск


class ApplyPauseTest(unittest.TestCase):
    """Пауза применения конфига: только восстановление выбора, пока конвейер работает — ничего."""

    def setUp(self):
        sw = FakeSwitcher()
        sw.active_regions = lambda: ["eu", "ai"]
        sw.active_node = lambda region: NODE
        self.reasserted = []
        sw.reassert_chain = lambda region, proxies: self.reasserted.append(region) or 0
        sw.fallback_regions = lambda: []
        self.calls, self.paused, self.held = [], True, False
        self.mon = ProductionMonitor(cfg=MonitorConfig(), api=FakeApi(), switcher=sw,
                                     prober=lambda region, node: self.calls.append(region) or GOOD,
                                     traffic=dict, pause_check=lambda: self.paused,
                                     hold_check=lambda: self.held)

    def test_pause_reasserts_without_probes(self):
        self.mon._tick()
        self.assertEqual(self.reasserted, ["eu", "ai"])
        self.assertEqual(self.calls, [])

    def test_pipeline_running_touches_nothing(self):
        self.held = True
        self.mon._tick()
        self.mon.reassert()
        self.assertEqual(self.reasserted, [])


if __name__ == "__main__":
    unittest.main()
