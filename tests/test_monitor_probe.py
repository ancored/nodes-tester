"""Зонд монитора: одиночный обрыв перепроверяется, страйк — только если провалился и повтор.
Ожидание срока ротации сообщает в лог один раз, а не на каждом шаге опроса."""

import contextlib
import io
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

    def evaluate_region(self, region, emergency=False):
        self.emergencies.append(region)


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
    def _probe(self, mon, st):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mon._probe_region("us", "node-us", st, 0.0)
        return out.getvalue()

    def test_single_break_retried_without_strike(self):
        mon, sw, calls = probe_with([BREAK, GOOD])
        st = {"strikes": 1}
        self._probe(mon, st)
        self.assertEqual(len(calls), 2)
        self.assertEqual(st["strikes"], 0)
        self.assertEqual(sw.emergencies, [])

    def test_break_twice_is_strike_with_error(self):
        mon, sw, calls = probe_with([BREAK, BREAK])
        st = {"strikes": 0}
        log = self._probe(mon, st)
        self.assertEqual(len(calls), 2)
        self.assertEqual(st["strikes"], 1)
        self.assertIn("ssl_read returned", log)
        self.assertEqual(sw.emergencies, [])

    def test_emergency_after_fails_failed_probes(self):
        mon, sw, _ = probe_with([BREAK] * 4)
        st = {"strikes": 0}
        self._probe(mon, st)
        self._probe(mon, st)
        self.assertEqual(sw.emergencies, ["us"])

    def test_no_retry_when_inconclusive_or_good(self):
        for first in (LIMITED, GOOD):
            with self.subTest(first=first):
                mon, sw, calls = probe_with([first])
                st = {"strikes": 1}
                self._probe(mon, st)
                self.assertEqual(len(calls), 1)
                self.assertEqual(st["strikes"], 0)


class WaitAnnounceTest(unittest.TestCase):
    def test_wait_announced_once(self):
        r = make_runner(temp_dir())
        try:
            clock = {"now": 1_000_000.0}

            class FakeTime:
                @staticmethod
                def time():
                    return clock["now"]

            def wait(seconds):
                clock["now"] += seconds
                return False

            out = io.StringIO()
            with patch("nodes_tester.runner.time", FakeTime), \
                    patch.object(r.switcher, "next_rotate_deadline",
                                 return_value=clock["now"] + 3600), \
                    patch.object(r, "_wait_interruptible", side_effect=wait), \
                    contextlib.redirect_stdout(out):
                r._wait_for_rotation()
            self.assertEqual(out.getvalue().count("ждём"), 1)
            self.assertIn("настал срок ротации", out.getvalue())
        finally:
            r.storage.close()


if __name__ == "__main__":
    unittest.main()
