"""Группы переключения из sing-box: пересекающиеся группы, кандидаты по членству,
активная нода в нескольких группах, цепочка селекторов без чужих групп."""

import unittest

from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir

EU = "DEMO-vless|reality-nl-out [aaaa0001]"
EU_AI = "DEMO-vless|reality-de-out [AI] [aaaa0002]"
US_AI = "DEMO-vless|reality-us-out [AI] [aaaa0003]"


def _row(node, region, score):
    return {"node": node, "region": region, "score": score, "id": node[-9:-1], "active": 0}


def _proxies():
    def sel(tag, members):
        return {"type": "selector", "all": [f"{tag}-failsafe"] + members,
                "now": f"{tag}-failsafe"}
    return {
        EU: {"type": "vless"}, EU_AI: {"type": "vless"}, US_AI: {"type": "vless"},
        "eu-auto-out": sel("eu-auto-out", [EU, EU_AI]),
        "us-auto-out": sel("us-auto-out", [US_AI]),
        "ai-auto-out": sel("ai-auto-out", [EU_AI, US_AI]),
        "eu-auto-out-failsafe": {"type": "urltest", "all": [EU, EU_AI]},
        "us-auto-out-failsafe": {"type": "urltest", "all": [US_AI]},
        "ai-auto-out-failsafe": {"type": "urltest", "all": [EU_AI, US_AI]},
        "global-auto-out": {"type": "selector", "all": ["eu-auto-out", "us-auto-out"],
                            "now": "eu-auto-out"},
        "nodes-tester": {"type": "selector", "all": [EU, EU_AI, US_AI], "now": EU},
    }


class _Api:
    def __init__(self):
        self.proxies = _proxies()
        self.selected = []

    def all_proxies(self):
        return self.proxies

    def select(self, group, node):
        self.selected.append((group, node))
        self.proxies[group]["now"] = node

    def list_group_members(self, group):
        return [t for t in self.proxies[group]["all"]]


class NodeGroupsTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())
        self.addCleanup(self.r.storage.close)
        self.r.api = _Api()
        self.r.cfg.run.node_group_overrides = {}      # без обязательных тестов групп
        self.board = self.r.board
        self.board.rows = {n: _row(n, reg, s) for n, reg, s in
                           ((EU, "eu", 80), (EU_AI, "eu", 90), (US_AI, "us", 70))}
        self.r._refresh_groups()

    def test_groups_from_singbox(self):
        self.assertEqual(sorted(self.board.regions()), ["ai", "eu", "us"])
        self.assertEqual(self.board.node_groups(EU_AI), ["eu", "ai"])
        self.assertEqual([c["node"] for c in self.board.candidates("ai")], [EU_AI, US_AI])
        self.assertEqual([c["node"] for c in self.board.candidates("eu")], [EU_AI, EU])

    def test_active_in_two_groups(self):
        self.board.set_active("eu", EU_AI)
        self.board.set_active("ai", EU_AI)
        self.board.set_active("eu", EU)                  # eu ушла на другую — в ai EU_AI осталась
        self.assertEqual(self.board.rows[EU_AI]["active"], 1)
        self.assertEqual(self.board.rows[EU]["active"], 1)
        self.assertEqual(self.board.rows[US_AI]["active"], 0)

    def test_switch_in_ai_does_not_touch_eu(self):
        sw = self.r.switcher
        sw.api = self.r.api
        self.assertTrue(sw.force_activate("ai", EU_AI))
        self.assertEqual(self.r.api.selected, [("ai-auto-out", EU_AI)])   # eu-auto-out не тронут
        self.assertEqual(sw.active_node("ai"), EU_AI)
        crcs = {row[0] for row in self.r.storage._db.execute(
            "SELECT crc FROM scores WHERE active = 1")}
        self.assertTrue(crcs <= {"aaaa0002"})

    def test_params_union_over_groups(self):
        self.r.cfg.run.node_group_overrides = {"ai": {"tests_enabled": ["gemini"],
                                                      "request_timeout": 30}}
        self.r._region_cache = {}
        p = self.r._node_params(parse_node(EU_AI))
        self.assertIn("gemini", p.tests_enabled)
        self.assertIn("download", p.tests_enabled)
        self.assertEqual(p.request_timeout, 30)
        plain = self.r._groups_params(["eu"])
        self.assertNotIn("gemini", plain.tests_enabled)

    def test_vanished_group_state_pruned(self):
        sw = self.r.switcher
        sw.state["other"] = {"active": EU, "recent": [], "activations": {}}
        self.r._refresh_groups()
        self.assertNotIn("other", sw.state)
        self.assertNotIn("other", sw.active_regions())

    def test_no_groups_falls_back_to_regions(self):
        self.r.api.proxies = {"nodes-tester": {"type": "selector", "all": [EU], "now": EU}}
        self.r._refresh_groups()
        self.assertEqual(sorted(self.board.regions()), ["eu", "us"])


if __name__ == "__main__":
    unittest.main()
