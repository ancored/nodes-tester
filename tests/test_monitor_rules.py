"""Монитор: авария по ошибкам соединений (monitor.errors), разбор строки лога sing-box,
зонд тишины только при соединениях."""

import contextlib
import io
import os
import time
import unittest

from nodes_tester.config import ErrorRuleConfig, MonitorConfig, parse_config
from nodes_tester.monitor import ProductionMonitor, drop_node, error_class, parse_drop, parse_error
from tests.helpers import temp_dir

NODE = "P-vless-out [aaaa0001.00000001]"
CRC = "aaaa0001.00000001"
GOOD = (True, 50.0, False, "")
REFUSED = "dial tcp 1.2.3.4:443: connect: connection refused"


class FakeApi:
    def all_proxies(self):
        return {NODE: {"type": "vless"}, "eu-auto-out": {"type": "selector", "now": NODE}}


class FakeSwitcher:
    def __init__(self, switched_at):
        self.switched_at = switched_at
        self.emergencies, self.penalized = [], []

    def active_regions(self):
        return ["eu"]

    def active_node(self, region):
        return NODE

    def reassert_chain(self, region, proxies):
        return 0

    def fallback_regions(self):
        return []

    def last_switch(self, region):
        return self.switched_at

    def penalize(self, node):
        self.penalized.append(node)

    def evaluate_region(self, region, emergency=False):
        self.emergencies.append(region)


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))


class ErrorRuleTest(unittest.TestCase):
    """За минуту ≥3 ошибок и за минуту до неё ≥2 — авария без зонда."""

    def setUp(self, switched_ago=3600, rule=None, group_rules=None):
        self.log = os.path.join(temp_dir(), "sing-box.log")
        open(self.log, "w").close()
        self.now = time.time()
        self.sw = FakeSwitcher(self.now - switched_ago)
        self.probes = []
        cfg = MonitorConfig(errors=rule or ErrorRuleConfig())
        self.mon = ProductionMonitor(cfg=cfg, api=FakeApi(), switcher=self.sw,
                                     prober=lambda region, node: self.probes.append(region) or GOOD,
                                     traffic=lambda: {CRC: 10 ** 9}, singbox_log=self.log,
                                     group_rules=group_rules)
        self._tick()                                     # первое чтение — с конца файла

    def _write(self, ago, error=REFUSED, age="5.0s", n=1):
        with open(self.log, "a", encoding="utf-8") as fh:
            for _ in range(n):
                fh.write(f"+0000 {stamp(self.now - ago)} ERROR [7 {age}] connection: open connection to "
                         f"8.8.8.8:443 using outbound/selector[eu-auto-out]: {error}\n")

    def _tick(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.mon._tick()

    def test_sustained_errors_switch_without_probe(self):
        self._write(90, n=2)                              # окно до последнего
        self._write(20, n=3)                              # последнее окно
        self._tick()
        self.assertEqual(self.sw.emergencies, ["eu"])
        self.assertEqual(self.sw.penalized, [NODE])
        self.assertEqual(self.probes, [])

    def test_one_minute_burst_is_not_enough(self):
        self._write(20, n=4)                              # всплеск без «до» — только зонд не дойдёт (<5)
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_errors_spread_over_ticks_add_up(self):
        self._write(100, n=2)
        self._tick()
        self._write(30, n=2)
        self._tick()
        self.assertEqual(self.sw.emergencies, [])
        self._write(10)
        self._tick()
        self.assertEqual(self.sw.emergencies, ["eu"])

    def test_fresh_node_switches_on_fewer_errors(self):
        self.setUp(switched_ago=120)
        self._write(10, n=2, age="1.0s")
        self._tick()
        self.assertEqual(self.sw.emergencies, ["eu"])

    def test_connections_started_before_switch_are_ignored(self):
        self.setUp(switched_ago=30)
        self._write(10, n=5, age="1m0s")                  # начаты через прежнюю ноду
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_canceled_and_block_out_are_not_counted(self):
        self._write(90, n=3, error="dial tcp 1.2.3.4:443: operation was canceled")
        self._write(20, n=4, error="operation not permitted")
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_group_rule_overrides(self):
        strict = ErrorRuleConfig(min=1, prev_min=0)
        self.setUp(group_rules={"eu": strict})
        self._write(20)
        self._tick()
        self.assertEqual(self.sw.emergencies, ["eu"])

    def test_probe_confirm_keeps_node_when_probe_passes(self):
        self.setUp(rule=ErrorRuleConfig(probe_confirm=True))
        self._write(90, n=2)
        self._write(20, n=3)
        self._tick()
        self.assertEqual(self.probes, ["eu"])
        self.assertEqual(self.sw.emergencies, [])


class DropRuleTest(unittest.TestCase):
    """Обрывы открытых соединений: ≥10 секунд с обрывами за 10 мин и ≥5 за 10 мин до них — авария."""

    def setUp(self, switched_ago=7200):
        self.log = os.path.join(temp_dir(), "sing-box.log")
        open(self.log, "w").close()
        self.now = time.time()
        self.sw = FakeSwitcher(self.now - switched_ago)
        self.probes, self.closed = [], []
        self.mon = ProductionMonitor(cfg=MonitorConfig(), api=FakeApi(), switcher=self.sw,
                                     prober=lambda region, node: self.probes.append(region) or GOOD,
                                     traffic=lambda: {CRC: 10 ** 9}, singbox_log=self.log,
                                     closed=lambda: list(self.closed))
        self._tick()

    def _drop(self, ago, age=93.0, crc=CRC, code="INTERNAL_ERROR", streams=1, busy=True):
        """Обрыв `streams` соединений одной секундой и их закрытия в потоке API."""
        t = int(self.now - ago)
        with open(self.log, "a", encoding="utf-8") as fh:
            for _ in range(streams):
                fh.write(f"+0000 {stamp(t)} ERROR [7 {int(age) // 60}m{int(age) % 60}s] connection: "
                         f"connection download closed: stream error: stream ID 3; {code}; received from peer\n")
        self.closed += [(t + 0.4, age + 0.2, crc, busy)] * streams

    def _tick(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.mon._tick()

    def test_sustained_drops_switch_without_probe(self):
        for ago in range(1100, 600, -100):                  # 5 в окне до последнего
            self._drop(ago)
        for ago in range(550, 50, -50):                     # 10 в последнем окне
            self._drop(ago)
        self._tick()
        self.assertEqual(self.sw.emergencies, ["eu"])
        self.assertEqual(self.sw.penalized, [NODE])
        self.assertEqual(self.probes, [])

    def test_streams_of_one_second_are_one_drop(self):
        for ago in range(1100, 600, -100):
            self._drop(ago)
        for ago in (500, 400, 300):
            self._drop(ago, streams=5)
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_other_node_and_clean_close_are_not_counted(self):
        for ago in range(1100, 600, -100):
            self._drop(ago)
        for ago in range(550, 50, -50):
            self._drop(ago, crc="bbbb0002.00000001" if ago % 100 else CRC)
            self._drop(ago - 1, code="NO_ERROR")
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_drop_waits_for_its_closed_connection(self):
        self._drop(1)
        self.closed.clear()
        self._tick()
        self.assertEqual(self.mon._unmatched, [(int(self.now - 1), 93.0)])
        self.closed.append((int(self.now - 1) + 0.4, 93.2, CRC, True))
        self._tick()
        self.assertEqual(self.mon._unmatched, [])
        self.assertEqual(len(self.mon._state["eu"]["drops"]), 1)

    def test_idle_connections_closed_by_server_are_not_counted(self):
        for ago in range(1100, 600, -100):
            self._drop(ago, busy=False)
        for ago in range(550, 50, -50):
            self._drop(ago, busy=False)
        self._tick()
        self.assertEqual(self.sw.emergencies, [])

    def test_connections_started_before_switch_are_ignored(self):
        self.setUp(switched_ago=60)
        for ago in range(55, 5, -5):
            self._drop(ago, age=120.0)
        self._tick()
        self.assertEqual(len(self.mon._state["eu"]["drops"]), 0)


class DropMatchTest(unittest.TestCase):
    def test_parse_drop(self):
        line = ("+0000 2026-10-28 03:00:44 ERROR [1389443394 1m18s] connection: connection download closed: "
                "stream error: stream ID 5; INTERNAL_ERROR; received from peer")
        logged, age = parse_drop(line, now=0)
        self.assertEqual((time.strftime("%H:%M:%S", time.gmtime(logged)), age), ("03:00:44", 78.0))
        for tail in ("stream error: stream ID 5; NO_ERROR; received from peer",
                     "stream error: stream ID 5; CANCEL; received from peer",
                     "http2: server sent GOAWAY and closed the connection; LastStreamID=5, ErrCode=NO_ERROR",
                     "stream 9392 canceled by remote with error code 0"):
            with self.subTest(tail=tail):
                self.assertIsNone(parse_drop(line.split("closed: ")[0] + "closed: " + tail, now=0))

    def test_match_needs_one_node(self):
        closed = [(100.6, 78.3, "a", True), (100.9, 12.0, "b", True)]
        self.assertEqual(drop_node(100.0, 78.0, closed), "a")
        self.assertIsNone(drop_node(100.0, 40.0, closed))                     # длительность не та
        self.assertIsNone(drop_node(110.0, 78.0, closed))                     # время не то
        self.assertIsNone(drop_node(100.0, 78.0, closed + [(101.0, 77.5, "b", True)]))   # две ноды
        self.assertIsNone(drop_node(100.0, 78.0, [(100.6, 78.3, "a", False)]))         # простаивало


class ParseErrorTest(unittest.TestCase):
    def test_start_from_age_and_offset(self):
        line = ("+0300 2026-10-21 22:07:30 ERROR [3774252907 1m5s] connection: open connection to "
                "8.8.8.8:443 using outbound/selector[eu-auto-out]: " + REFUSED)
        tag, error, logged, started = parse_error(line, now=0)
        self.assertEqual((tag, error), ("eu-auto-out", REFUSED))
        self.assertEqual(time.strftime("%H:%M:%S", time.gmtime(logged)), "19:07:30")
        self.assertEqual(logged - started, 65)

    def test_without_timestamp(self):
        line = ("ERROR [1 202ms] connection: open connection to 8.8.8.8:443 using "
                "outbound/selector[ai-auto-out]: EOF")
        tag, _error, logged, started = parse_error(line, now=1000.0)
        self.assertEqual((tag, logged), ("ai-auto-out", 1000.0))
        self.assertAlmostEqual(started, 999.798)

    def test_classes(self):
        self.assertEqual(error_class(REFUSED), "connect")
        self.assertEqual(error_class("dial tcp 1.2.3.4:443: i/o timeout"), "connect")
        self.assertEqual(error_class("context deadline exceeded"), "timeout")
        self.assertEqual(error_class("read tcp a->b: read: connection reset by peer"), "reset")
        self.assertEqual(error_class("context canceled"), "canceled")
        self.assertIsNone(error_class("lookup x: i/o timeout"))
        self.assertIsNone(error_class("lookup x: unexpected EOF"))             # отказ DNS, не ноды
        self.assertIsNone(error_class("lookup x: context deadline exceeded"))


class SilenceNeedsConnectionsTest(unittest.TestCase):
    """Тихо дольше silence: зонд, только если через ноду были соединения."""

    def setUp(self):
        self.sw = FakeSwitcher(0)
        self.probes, self.conns = [], {CRC: 0}
        self.mon = ProductionMonitor(cfg=MonitorConfig(silence=300), api=FakeApi(), switcher=self.sw,
                                     prober=lambda region, node: self.probes.append(region) or GOOD,
                                     traffic=dict, conns=lambda: dict(self.conns))

    def _tick_at(self, t):
        with unittest.mock.patch("nodes_tester.monitor.time.monotonic", return_value=t), \
                contextlib.redirect_stdout(io.StringIO()):
            self.mon._tick()

    def test_idle_group_is_not_probed(self):
        self._tick_at(0)
        self._tick_at(301)
        self.assertEqual(self.probes, [])

    def test_connections_without_traffic_are_probed(self):
        self._tick_at(0)
        self.conns[CRC] = 7
        self._tick_at(301)
        self.assertEqual(self.probes, ["eu"])


class ConfigRuleTest(unittest.TestCase):
    def test_group_rule_inherits_monitor_errors(self):
        cfg = parse_config({"monitor": {"errors": {"window": "2m", "min": 4}},
                            "groups": [{"tag": "ai", "monitor": {"errors": {"min": 1}}}, {"tag": "eu"}]})
        ai, eu = cfg.groups
        self.assertEqual((ai.errors.window, ai.errors.min, ai.errors.prev_min), (120.0, 1, 2))
        self.assertIsNone(eu.errors)

    def test_drops_rule(self):
        drops = parse_config({"monitor": {"drops": {"window": "5m", "min": 6}}}).monitor.drops
        self.assertEqual((drops.enabled, drops.window, drops.min, drops.prev_min), (True, 300.0, 6, 5))

    def test_bad_values_are_errors(self):
        from nodes_tester.config import ConfigError
        for data in ({"monitor": {"errors": {"classes": ["dns"]}}},
                     {"monitor": {"outage": {"targets": ["a.ru:443"], "fail_quorum": 2}}},
                     {"monitor": {"outage": {"targets": ["no-port"]}}}):
            with self.subTest(data=data), self.assertRaises(ConfigError):
                parse_config(data)


import unittest.mock  # noqa: E402 — patch выше в SilenceNeedsConnectionsTest

if __name__ == "__main__":
    unittest.main()
