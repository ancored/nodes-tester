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

    mon = ProductionMonitor(cfg=MonitorConfig(enabled=True, fails=2), api=None,
                            switcher=sw, prober=prober, traffic=dict)
    return mon, sw, calls


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
