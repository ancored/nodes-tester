"""Write/control-эндпоинты админки (Фаза 2-3): карантин/бан/switch/status/run/logs.

Проверяем без сети: фейковый Runner поверх реального Storage (временная БД) и
прямые вызовы `app.handle`. Read-only режим (runner=None) должен отвечать 409.
"""

from __future__ import annotations

import io
import json
import os
import threading
import unittest

from tests.helpers import make_config, temp_dir, fresh_nodes_json
from nodes_tester.config import load_config
from nodes_tester.logbuffer import LogRing
from nodes_tester.storage import Storage
from nodes_tester.vacuum import Vacuum
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
        self._requested = False
        self.restricted = []

    def restrict_node(self, crc, on, entry=None):
        self.restricted.append((crc, on))

    def restricted_crcs(self):
        return set()

    def request_tests(self):
        self._requested = True

    def status(self):
        return {
            "running": self._running, "switching": True, "monitor": False, "traffic": False,
            "regions": [{"region": "eu", "active": "LUNA-vless|reality-out [abcd1234.0000beef]"}],
            "next_rotation": None,
        }


class ApiControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_config(make_config(self.tmp,
                                           admin={"enabled": True, "token": "secret"}))
        self.storage = Storage(self.cfg.storage)
        self.node_tag = "LUNA-vless|reality-out [abcd1234.0000beef]"
        fresh_nodes_json(self.tmp, [{"tag": self.node_tag, "type": "vless",
                                     "server": "1.2.3.4", "server_port": 443}])
        self.storage.load_nodes(os.path.join(self.tmp, "nodes.json"))
        self.runner = FakeRunner(self.cfg, self.storage)
        self.runner.vacuum = Vacuum(self.storage, self.cfg.storage.vacuum)
        self.app = build_app(self.cfg, runner=self.runner)

    def tearDown(self):
        self.storage.close()

    def _call(self, method, path, token="secret", body=None):
        headers = {"X-Admin-Token": token}
        return self.app.handle(method, path, headers,
                               json.dumps(body).encode() if body is not None else b"")

    def test_ban_and_unban(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234.0000beef/ban").status, 200)
        self.assertIn("abcd1234.0000beef", self.storage.banned_crcs())
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234.0000beef/unban").status, 200)
        self.assertNotIn("abcd1234.0000beef", self.storage.banned_crcs())
        self.assertEqual(self.runner.restricted, [("abcd1234.0000beef", True), ("abcd1234.0000beef", False)])

    def test_quarantine_sets_backoff(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234.0000beef/quarantine").status, 200)
        backoff = self.storage.load_backoff()
        self.assertIn("abcd1234.0000beef", backoff)
        self.assertEqual(backoff["abcd1234.0000beef"][2], "garbage")
        # снятие карантина
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234.0000beef/unquarantine").status, 200)
        self.assertNotIn("abcd1234.0000beef", self.storage.load_backoff())

    def test_force_switch(self):
        self.assertEqual(self._call("POST", "/api/groups/eu/switch",
                                    body={"node": "X"}).status, 200)
        self.assertEqual(self.runner.switcher.forced, [("eu", "X")])

    def test_force_switch_requires_node(self):
        self.assertEqual(self._call("POST", "/api/groups/eu/switch", body={}).status, 400)

    def test_status_and_run_tests(self):
        r = self._call("GET", "/api/status")
        self.assertEqual(r.status, 200)
        self.assertTrue(json.loads(r.body)["running"])
        self.assertEqual(self._call("POST", "/api/run/tests").status, 200)
        self.assertTrue(self.runner._requested)

    def test_vacuum_state_and_manual_run(self):
        state = json.loads(self._call("GET", "/api/storage/vacuum").body)
        self.assertIsNone(state["last"])               # только отсчёт расписания, запусков не было
        self.assertGreater(state["next"], 0)
        self.assertEqual(self._call("GET", "/api/storage/vacuum", token="bad").status, 401)
        self.assertEqual(self._call("POST", "/api/storage/vacuum").status, 200)
        self.runner.vacuum._thread.join(10)
        last = json.loads(self._call("GET", "/api/storage/vacuum").body)["last"]
        self.assertEqual(last["trigger"], "manual")
        self.assertGreater(last["size_before"], 0)
        self.assertIn("size_after", last)

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
        r = self.app.handle("POST", "/api/groups/eu/switch", headers, b"[]")
        self.assertEqual(r.status, 400)

    def test_node_actions_reject_unknown_crc(self):
        self.assertEqual(self._call("POST", "/api/nodes/not-a-crc/ban").status, 400)
        self.assertEqual(self._call("POST", "/api/nodes/deadbeef.00000000/ban").status, 404)
        self.assertEqual(self._call("POST", "/api/nodes/deadbeef.00000000/quarantine").status, 404)

    def test_control_requires_token(self):
        self.assertEqual(self._call("POST", "/api/nodes/abcd1234.0000beef/ban",
                                    token="WRONG").status, 401)

    def test_readonly_mode_returns_409(self):
        app2 = build_app(self.cfg, runner=None)
        r = app2.handle("POST", "/api/nodes/abcd1234.0000beef/ban",
                        {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r.status, 409)
        r2 = app2.handle("GET", "/api/status", {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r2.status, 409)


class LogRingTest(unittest.TestCase):
    def test_streams_and_threads_do_not_glue_lines(self):
        from nodes_tester.logbuffer import TeeStream
        ring = LogRing()
        out, err = TeeStream(ring, io.StringIO()), TeeStream(ring, io.StringIO())
        out.write("из stdout")
        err.write("из stderr\n")                      # между текстом и переводом stdout
        worker = threading.Thread(target=lambda: out.write("из потока\n"))
        worker.start()
        worker.join()
        out.write("\n")
        self.assertEqual([e["line"] for e in ring.snapshot()], ["из stderr", "из потока", "из stdout"])


if __name__ == "__main__":
    unittest.main()
