"""Пересборка по деградации: только когда группа деградировала дольше порога И свежий опрос
это исправит; не чаще min_interval."""

import json
import os
import unittest

from nodes_tester.config import parse_config
from nodes_tester.rebuild import RebuildWatch
from tests.helpers import temp_dir
from tests.test_nodes_config import _raw, _vless


class Board:
    def __init__(self):
        self.candidates_list = []

    def regions(self):
        return ["ai"]

    def candidates(self, group):
        return self.candidates_list

    def dead_keys(self):
        return {"11111111.22222222"}     # единственная нода конфига измерена и мертва


class Orch:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.started = []
        self.last = None

    def last_started(self, modes):
        return self.last

    def start_run(self, mode, dry_run, trigger="manual"):
        self.started.append((mode, dry_run, trigger))


class Notifier:
    def __init__(self):
        self.sent = []

    def send(self, event, text, key=None):
        self.sent.append((event, key))


class RebuildWatchTest(unittest.TestCase):
    def setUp(self):
        tmp = temp_dir()
        os.makedirs(os.path.join(tmp, "raw"))
        with open(os.path.join(tmp, "raw", "router.json"), "w", encoding="utf-8") as fh:
            json.dump(_raw([("A", "x", _vless("10.0.0.1")), ("A", "y", _vless("10.0.0.2"))]), fh)
        self.cfg = parse_config({"subscriptions": [{"tag": "A", "url": "https://a"}],
                                 "groups": [{"tag": "ai", "strict": True}]})
        self.cfg.nodes_file = os.path.join(tmp, "nodes.json")
        # в действующем конфиге — одна нода, которой в подписке уже нет
        with open(self.cfg.nodes_file, "w", encoding="utf-8") as fh:
            json.dump({"outbounds": [{"type": "vless", "tag": "A-vless|reality-out [11111111.22222222]"}]}, fh)
        self.now = [1000.0]
        self.board, self.orch, self.notifier = Board(), Orch(tmp), Notifier()
        switcher = type("S", (), {"state": {"ai": {"failsafe": True, "active": None}}})()
        self.watch = RebuildWatch(self.cfg, self.board, switcher, lambda: self.orch, self.notifier,
                                  clock=lambda: self.now[0])

    def test_rebuilds_after_degraded_for_and_respects_min_interval(self):
        self.assertEqual(self.watch.check(), "")                 # деградация только началась
        self.now[0] += 15 * 60
        reason = self.watch.check()
        self.assertIn("группа ai без кандидатов", reason)
        self.assertIn("новых доступов в подписках: 2", reason)
        self.assertEqual(self.orch.started, [("router", False, "rebuild")])
        self.assertEqual(self.notifier.sent, [("pipeline", "rebuild")])
        self.orch.last = self.now[0]                             # сборка только что была
        self.now[0] += 20 * 60
        self.watch.check()
        self.now[0] += 20 * 60
        self.assertEqual(self.watch.check(), "")
        self.assertEqual(len(self.orch.started), 1)

    def test_no_rebuild_when_fresh_poll_does_not_help(self):
        # те же доступы, что в подписке: пересборка ничего не даст
        from nodes_common import raw as rawfmt
        from nodes_config import build
        raw = rawfmt.load(os.path.join(self.orch.data_dir, "raw", "router.json"))
        fragment, _ = build.build([raw], self.cfg, "router", log=lambda m: None)
        with open(self.cfg.nodes_file, "w", encoding="utf-8") as fh:
            json.dump(fragment, fh)
        self.watch.check()
        self.now[0] += 3600
        self.assertEqual(self.watch.check(), "")
        self.assertEqual(self.orch.started, [])

    def test_disabled_router_build_and_healthy_group(self):
        self.cfg.pipeline.router.enabled = False
        self.watch.check()
        self.now[0] += 3600
        self.assertEqual(self.watch.check(), "")
        self.cfg.pipeline.router.enabled = True
        self.watch.switcher.state["ai"] = {"active": "A-vless|reality-out [x]"}
        self.board.candidates_list = [{}] * 4                    # резерв 3 + активная
        self.now[0] += 3600
        self.assertEqual(self.watch.check(), "")


if __name__ == "__main__":
    unittest.main()
