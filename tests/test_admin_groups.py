"""Группы нод в админке: состав из nodes.json, ручной выбор в группе, предпросмотр."""

from __future__ import annotations

import json
import os
import unittest

from tests.helpers import make_config, temp_dir, fresh_nodes_json
from tests.test_api_control import FakeRunner
from nodes_tester.config import load_config
from nodes_tester.storage import Storage
from dashboard import data
from dashboard.api_config import preview
from dashboard.server import build_app
from dashboard.webapp import HttpError
from naming import content_crc32

EU = "LUNA-trojan-nl-out [aaaa0001]"
US_FRAG = {"type": "trojan", "server": "1.2.3.4", "server_port": 443}
US_CRC = content_crc32(US_FRAG)
US = f"LUNA-trojan-us-out [{US_CRC}]"


def _nodes_json(path, groups):
    obs = [{"type": "trojan", "tag": t} for t in (EU, US)]
    for g, members in groups.items():
        obs.append({"type": "selector", "tag": f"{g}-auto-out", "outbounds": [f"{g}-auto-out-failsafe"] + members})
        obs.append({"type": "urltest", "tag": f"{g}-auto-out-failsafe", "outbounds": members})
    obs.append({"type": "urltest", "tag": "global-auto-out-failsafe", "outbounds": ["eu-auto-out-failsafe"]})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"outbounds": obs}, fh)


class NodeGroupsSnapshotTest(unittest.TestCase):
    def test_groups_from_failsafe_members(self):
        tmp = temp_dir()
        path = os.path.join(tmp, "nodes.json")
        _nodes_json(path, {"eu": [EU], "us": [US], "ai": [US]})
        order, by_crc = data.node_groups(path)
        self.assertEqual(order, ["eu", "us", "ai"])            # global-* не группа
        self.assertEqual(by_crc, {"aaaa0001": ["eu"], US_CRC: ["us", "ai"]})
        self.assertEqual(data.node_groups(os.path.join(tmp, "missing.json")), ([], {}))

    def test_provider_quality_counts_node_in_each_group(self):
        rows = data._provider_quality([
            {"provider": "LUNA", "score": 50, "groups": ["us", "ai"]},
            {"provider": "LUNA", "score": 0, "groups": ["us"]},
            {"provider": "LUNA", "score": 10, "groups": []}])
        by = {r["group"]: r for r in rows}
        self.assertEqual(by["us"]["total"], 2)
        self.assertEqual(by["us"]["dead"], 1)
        self.assertEqual(by["ai"]["total"], 1)
        self.assertEqual(by["вне групп"]["total"], 1)


class Board:
    def __init__(self, members):
        self.members = members

    def regions(self):
        return list(self.members)

    def candidates(self, group):
        return [{"node": n} for n in self.members[group]]


class GroupSwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_config(make_config(self.tmp, dashboard={"enabled": True, "token": "secret"}))
        self.storage = Storage(self.cfg.storage)
        fresh_nodes_json(self.tmp, [{"tag": US, **US_FRAG}])
        self.storage.load_nodes(os.path.join(self.tmp, "nodes.json"))
        self.storage.reconcile_presence([US_CRC])
        self.runner = FakeRunner(self.cfg, self.storage)
        self.runner.board = Board({"eu": [], "ai": [US]})
        self.runner.restricted_crcs = lambda: set()
        self.app = build_app(self.cfg, runner=self.runner)

    def tearDown(self):
        self.storage.close()

    def _switch(self, path):
        return self.app.handle("POST", path, {"X-Admin-Token": "secret"}, json.dumps({"node": US}).encode())

    def test_groups_route_and_legacy_alias(self):
        self.assertEqual(self._switch("/api/groups/ai/switch").status, 200)
        self.assertEqual(self._switch("/api/regions/ai/switch").status, 200)
        self.assertEqual(self.runner.switcher.forced, [("ai", US), ("ai", US)])

    def test_not_candidate_returns_allowed_groups(self):
        self.runner.switcher.force_activate = lambda group, node: False
        r = self._switch("/api/groups/eu/switch")
        self.assertEqual(r.status, 409)
        body = json.loads(r.body)
        self.assertEqual(body["code"], "not_candidate")
        self.assertEqual(body["allowed_groups"], ["ai"])

    def test_snapshot_lists_activate_groups(self):
        r = self.app.handle("GET", "/api/data", {"X-Admin-Token": "secret"}, b"")
        node = next(n for n in json.loads(r.body)["nodes"] if n["node"] == US)
        self.assertEqual(node["activate_groups"], ["ai"])
        self.assertTrue(node["can_activate"])


class GroupPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.raw = os.path.join(self.tmp, "main.json")
        nodes = [{"provider": "LUNA", "title": f"{cc.upper()} {i}", "cc_hint": cc,
                  "outbound": {"type": "trojan", "server": f"{cc}{i}.example", "server_port": 443,
                               "password": "x", "tls": {"enabled": True}}}
                 for i, cc in enumerate(["nl", "de", "us"])]
        with open(self.raw, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "sources": [], "nodes": nodes}, fh)
        self.nodes_file = os.path.join(self.tmp, "nodes.json")
        _nodes_json(self.nodes_file, {"eu": [EU], "old": [US]})

    def test_counts_fallback_and_diff(self):
        result = preview({"groups": [
            {"name": "eu", "match": {"regions": ["eu"]}},
            {"name": "us", "match": {"countries": ["us"]}},
            {"name": "jp", "match": {"countries": ["jp"]}}]},
            self.raw, os.path.join(self.tmp, "none.json"), self.nodes_file)
        by = {g["name"]: g for g in result["groups"]}
        self.assertEqual(by["eu"]["count"], 2)
        self.assertEqual(by["us"]["count"], 1)
        self.assertTrue(by["jp"]["fallback"])                  # пусто → все ноды
        self.assertEqual(by["jp"]["count"], 3)
        self.assertEqual(by["eu"]["overlaps"], {"jp": 2})
        self.assertEqual(result["added"], ["us", "jp"])
        self.assertEqual(result["removed"], ["old"])
        self.assertEqual(result["ungrouped"], 0)

    def test_missing_raw_is_conflict(self):
        with self.assertRaises(HttpError) as ctx:
            preview({"groups": []}, os.path.join(self.tmp, "no.json"), "", self.nodes_file)
        self.assertEqual(ctx.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
