"""Админка: состояние групп (/api/groups) и проверки групп для ноды (/api/nodes/{key}/groups)."""

import json
import time
import unittest

from dashboard.server import build_app
from tests.helpers import make_runner, temp_dir

A = "DEMO-vless-out [aaaa0001.00000001]"
B = "DEMO-vless-out [bbbb0002.00000002]"


def _row(node, score, exit_cc):
    return {"node": node, "region": "eu", "score": score, "provider": "DEMO", "protocol": "vless",
            "country": exit_cc, "id": node[-18:-1], "active": 0, "samples": 5, "heavy_ok": "",
            "heavy_ts": 0, "exit_cc": exit_cc, "country_ts": int(time.time()) - 60}


class ApiGroupsTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), tags=[A, B], admin={"token": "t"}, groups=[
            {"tag": "eu", "require": {"country": {"regions": ["eu"]}}},
            {"tag": "ai", "strict": True, "in_global": False, "require": {"gemini": {}}}])
        self.addCleanup(self.r.storage.close)
        board = self.r.board
        board.rows = {A: _row(A, 80, "NL"), B: _row(B, 60, "US")}
        board.set_groups({"eu": {A, B}, "ai": {A, B}})
        self.r.switcher.state = {"eu": {"active": A},
                                 "ai": {"active": None, "failsafe": True, "fallback": "block-out"}}
        self.app = build_app(self.r.cfg, runner=self.r)

    def _get(self, path):
        r = self.app.handle("GET", path, {"X-Admin-Token": "t"}, b"")
        self.assertEqual(r.status, 200, r.body)
        return json.loads(r.body)

    def test_groups_state(self):
        groups = {g["tag"]: g for g in self._get("/api/groups")["groups"]}
        eu, ai = groups["eu"], groups["ai"]
        self.assertEqual((eu["active"], eu["fallback"], eu["candidates"], eu["members"]), (A, None, 1, 2))
        self.assertEqual(eu["top"], [{"node": A, "score": 80.0, "tier": 0}])
        self.assertEqual(eu["require"], ["country"])
        self.assertEqual((ai["active"], ai["fallback"], ai["candidates"], ai["strict"]), (None, "block-out", 0, True))

    def test_node_checks_with_age(self):
        data = self._get("/api/nodes/bbbb0002.00000002/groups")
        by = {g["group"]: g for g in data["groups"]}
        self.assertTrue(by["eu"]["member"])
        self.assertFalse(by["eu"]["candidate"])                     # US не в регионе eu
        country = by["eu"]["checks"][0]
        self.assertEqual((country["check"], country["part"], country["verdict"]), ("country", "require", "0"))
        self.assertGreater(country["ts"], 0)
        self.assertEqual(by["ai"]["checks"], [{"check": "gemini", "part": "require", "verdict": "", "ts": None}])
        self.assertEqual(self._get("/api/nodes/cccc0003.00000003/groups")["groups"], [])
        bad = self.app.handle("GET", "/api/nodes/nope/groups", {"X-Admin-Token": "t"}, b"")
        self.assertEqual(bad.status, 400)


if __name__ == "__main__":
    unittest.main()
