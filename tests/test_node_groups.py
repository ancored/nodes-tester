"""Группы переключения из sing-box: пересекающиеся группы, кандидаты по членству,
активная нода в нескольких группах, цепочка селекторов без чужих групп."""

import unittest

from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir

EU = "DEMO-vless|reality-out [aaaa0001.00000001]"
EU_AI = "DEMO-vless|reality-out [aaaa0002.00000001]"
US_AI = "DEMO-vless|reality-out [aaaa0003.00000001]"
GROUPS = [{"tag": "eu"}, {"tag": "us"}, {"tag": "ai", "in_global": False}]


def _row(node, region, score):
    return {"node": node, "region": region, "score": score, "id": parse_node(node).node_id, "active": 0}


def _proxies():
    # Состав, как раньше задавали условия стран (сборка теперь кладёт все ноды во все группы,
    # но тестер обязан работать и с выборочным составом).
    def sel(tag, members):
        return {"type": "selector", "all": ["failsafe-out"] + members, "now": "failsafe-out"}
    return {
        EU: {"type": "vless"}, EU_AI: {"type": "vless"}, US_AI: {"type": "vless"},
        "eu-auto-out": sel("eu-auto-out", [EU, EU_AI]),
        "us-auto-out": sel("us-auto-out", [US_AI]),
        "ai-auto-out": sel("ai-auto-out", [EU_AI, US_AI]),
        "failsafe-out": {"type": "urltest", "all": [EU, EU_AI, US_AI]},
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
        self.r = make_runner(temp_dir(), groups=GROUPS)
        self.addCleanup(self.r.storage.close)
        self.r.api = _Api()
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
        self.assertTrue(crcs <= {"aaaa0002.00000001"})

    def test_vanished_group_state_pruned(self):
        sw = self.r.switcher
        sw.state["gone"] = {"active": EU, "recent": [], "activations": {}}
        self.r._refresh_groups()
        self.assertNotIn("gone", sw.state)
        self.assertNotIn("gone", sw.active_regions())

    def test_no_group_selectors_means_no_groups(self):
        # В sing-box нет {tag}-auto-out (база без групп) — выбирать некуда, а не «группа = регион».
        self.r.api.proxies = {"nodes-tester": {"type": "selector", "all": [EU], "now": EU}}
        self.r._refresh_groups()
        self.assertEqual(self.board.regions(), [])


if __name__ == "__main__":
    unittest.main()
