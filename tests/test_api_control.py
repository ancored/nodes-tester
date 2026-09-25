"""Write/control-эндпоинты админки (Фаза 2-3): карантин/бан/switch/status/run/logs.

Проверяем без сети: фейковый Runner поверх реального Storage (временная БД) и
прямые вызовы `app.handle`. Read-only режим (runner=None) должен отвечать 409.
"""

from __future__ import annotations

import json
import os
import unittest

from tests.helpers import make_config, temp_dir, fresh_nodes_json
from nodes_tester.config import load_config
from nodes_tester.logbuffer import LogRing
from nodes_tester.storage import Storage
from dashboard.server import build_app


class FakeSwitcher:
    def __init__(self):
        self.forced = []

    def force_activate(self, region, node):
        self.forced.append((region, node))
        return True


class FakeRunner:
    def __init__(self, cfg, storage):
        self.cfg = cfg
        self.storage = storage
        self.switcher = FakeSwitcher()
        self.log = LogRing()
        self._running = True
        self._current_pass = 7
        self._current_day = "2026-09-20"
        self._requested = False

    def request_pass(self):
        self._requested = True

    def status(self):
        return {
            "running": self._running, "pass": self._current_pass,
            "day": self._current_day, "switching": True, "monitor": False,
            "traffic": False, "rotation_bound": False,
            "regions": [{"region": "eu", "active": "LUNA-vless|reality-nl-out [abcd1234]"}],
            "next_rotation": None,
        }


class ApiControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_config(make_config(self.tmp,
                                           dashboard={"enabled": True, "token": "secret"}))
        self.storage = Storage(self.cfg.storage)
        self.node_tag = "LUNA-vless|reality-nl-out [abcd1234]"
        fresh_nodes_json(self.tmp, [{"tag": self.node_tag, "type": "vless",
                                     "server": "1.2.3.4", "server_port": 443}])
        self.storage.load_nodes(os.path.join(self.tmp, "nodes.json"))
        self.runner = FakeRunner(self.cfg, self.storage)
        self.app = build_app(self.cfg, runner=self.runner)

    def tearDown(self):
        self.storage.close()

    def _call(self, method, path, token="secret", body=None):
        headers = {"X-Admin-Token": token}
        return self.app.handle(method, path, headers,
                               json.dumps(body).encode() if body is not None else b"")

    def test_ban_and_unban(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234/ban").status, 200)
        self.assertIn("abcd1234", self.storage.banned_crcs())
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234/unban").status, 200)
        self.assertNotIn("abcd1234", self.storage.banned_crcs())

    def test_quarantine_sets_backoff(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234/quarantine").status, 200)
        backoff = self.storage.load_backoff()
        self.assertIn("abcd1234", backoff)
        self.assertEqual(backoff["abcd1234"][3], "garbage")
        # снятие карантина
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234/unquarantine").status, 200)
        self.assertNotIn("abcd1234", self.storage.load_backoff())

    def test_force_switch(self):
        self.assertEqual(self._call("POST", "/api/regions/eu/switch",
                                    body={"node": "X"}).status, 200)
        self.assertEqual(self.runner.switcher.forced, [("eu", "X")])

    def test_force_switch_requires_node(self):
        self.assertEqual(self._call("POST", "/api/regions/eu/switch", body={}).status, 400)

    def test_status_and_run_pass(self):
        r = self._call("GET", "/api/status")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.body)["pass"], 7)
        self.assertEqual(self._call("POST", "/api/run/pass").status, 200)
        self.assertTrue(self.runner._requested)

    def test_logs(self):
        self.runner.log.write("hello\nworld\n")
        r = self._call("GET", "/api/logs?seq=0&tail=10")
        self.assertEqual(r.status, 200)
        body = json.loads(r.body)
        self.assertEqual([e["line"] for e in body["lines"]], ["hello", "world"])
        # инкрементальная подгрузка по seq
        last = body["seq"]
        self.runner.log.write("next\n")
        r2 = self._call("GET", f"/api/logs?seq={last}&tail=10")
        self.assertEqual([e["line"] for e in json.loads(r2.body)["lines"]], ["next"])

    def test_logs_reject_bad_query(self):
        self.assertEqual(self._call("GET", "/api/logs?seq=oops").status, 400)
        self.assertEqual(self._call("GET", "/api/logs?tail=-1").status, 400)

    def test_switch_rejects_non_object_json(self):
        headers = {"X-Admin-Token": "secret"}
        r = self.app.handle("POST", "/api/regions/eu/switch", headers, b"[]")
        self.assertEqual(r.status, 400)

    def test_node_actions_reject_unknown_crc(self):
        self.assertEqual(self._call("POST", "/api/nodes/not-a-crc/ban").status, 400)
        self.assertEqual(self._call("POST", "/api/nodes/deadbeef/ban").status, 404)
        self.assertEqual(self._call("POST", "/api/nodes/deadbeef/quarantine").status, 404)

    def test_control_requires_token(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234/ban",
                                    token="WRONG").status, 401)

    def test_readonly_mode_returns_409(self):
        app2 = build_app(self.cfg, runner=None)
        r = app2.handle("POST", "/api/nodes/abcd1234/ban",
                        {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r.status, 409)
        r2 = app2.handle("GET", "/api/status", {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r2.status, 409)


if __name__ == "__main__":
    unittest.main()
