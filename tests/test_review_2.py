"""Регрессии по ревью 2026-09-21: lifecycle Runner и присутствие banned-нод."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from types import SimpleNamespace

from tests.helpers import fresh_nodes_json, make_runner, temp_dir


class RunnerLifecycleRegressionTest(unittest.TestCase):
    def test_request_during_last_non_loop_pass_schedules_another(self):
        runner = make_runner(temp_dir())
        runner._base.loop = False
        runner._base.rounds = 1
        self.assertFalse(runner._should_continue(1))
        runner.request_pass()
        self.assertTrue(runner._should_continue(1))

    def test_invalid_initial_config_restores_process_state(self):
        tmp = temp_dir()
        runner = make_runner(tmp)
        runner.cfg.run.default.tests_enabled = []
        stdout, stderr = sys.stdout, sys.stderr
        try:
            with self.assertRaisesRegex(ValueError, "tests_enabled"):
                runner.run()
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
            self.assertFalse(runner.status()["running"])
        finally:
            # Защита самого тест-сьюта при прогоне против старой реализации.
            sys.stdout, sys.stderr = stdout, stderr
            runner._running = False
            if runner.storage is not None:
                try:
                    runner.storage.close()
                except Exception:
                    pass


class BannedPresenceRegressionTest(unittest.TestCase):
    def test_banned_node_stays_present_and_refreshes_last_seen(self):
        tmp = temp_dir()
        tag = "LUNA-vless|reality-nl-out [abcd1234]"
        runner = make_runner(tmp, tags=[tag])
        try:
            nodes_path = fresh_nodes_json(tmp, [{
                "tag": tag,
                "type": "vless",
                "server": "1.2.3.4",
                "server_port": 443,
            }])
            runner.storage.load_nodes(nodes_path)
            runner.storage.reconcile_presence(["abcd1234"])
            runner.storage.set_banned("abcd1234", True)
            runner._banned = runner.storage.banned_crcs()
            old = int(time.time()) - 10_000
            with runner.storage._lock:
                runner.storage._db.execute(
                    "UPDATE nodes SET last_seen = ? WHERE crc = ?", (old, "abcd1234"))
                runner.storage._db.commit()

            self.assertEqual(runner._enumerate_nodes(), [])
            # Перечень физически присутствующих CRC должен быть независим от test-фильтра.
            runner.storage.touch_seen(runner._present_crcs)
            runner.storage.reconcile_presence(runner._present_crcs)

            with runner.storage._lock:
                row = runner.storage._db.execute(
                    "SELECT present, last_seen, banned FROM nodes WHERE crc = ?",
                    ("abcd1234",),
                ).fetchone()
            self.assertEqual(row[0], 1)
            self.assertGreater(row[1], old)
            self.assertEqual(row[2], 1)
        finally:
            runner.storage.close()


class StaticServingRegressionTest(unittest.TestCase):
    def test_backslash_cannot_escape_static_root_on_windows(self):
        from dashboard.webapp import App

        root = temp_dir()
        static = os.path.join(root, "static")
        os.makedirs(os.path.join(static, "x"))
        secret = os.path.join(root, "secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("outside")
        app = App(SimpleNamespace(), static_dir=static)
        response = app.handle("GET", "/x\\..\\..\\secret.txt", {}, b"")
        self.assertEqual(response.status, 404)
        self.assertNotIn(b"outside", response.body)


class DashboardDataRegressionTest(unittest.TestCase):
    def test_readonly_query_supports_hash_in_database_path(self):
        from dashboard.data import _query

        path = os.path.join(temp_dir(), "stats#router.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE sample (value TEXT)")
        con.execute("INSERT INTO sample VALUES ('ok')")
        con.commit()
        con.close()
        self.assertEqual(_query(path, "SELECT value FROM sample"), [{"value": "ok"}])


class SwitcherRegressionTest(unittest.TestCase):
    class Board:
        def __init__(self, rows):
            self.rows = {r["node"]: r for r in rows}

        def get(self, node):
            return self.rows.get(node)

        def candidates(self, region):
            return [r for r in self.rows.values()
                    if r.get("region") == region and float(r.get("score", 0)) > 0]

        def set_active(self, region, node):
            pass

    class Clash:
        def __init__(self, fail_on=None):
            self.fail_on = fail_on
            self.selected = []

        def all_proxies(self):
            return {
                "EU-node": {"type": "vless", "all": []},
                "US-node": {"type": "vless", "all": []},
                "eu-auto-out": {"type": "selector", "all": ["EU-node", "US-node"]},
                "prod": {"type": "selector", "all": ["eu-auto-out"]},
            }

        def select(self, group, node):
            from nodes_tester.clash_api import ClashApiError
            if group == self.fail_on:
                raise ClashApiError("injected")
            self.selected.append((group, node))

    def setUp(self):
        self.runner = make_runner(temp_dir())
        self.sw = self.runner.switcher

    def tearDown(self):
        self.runner.storage.close()

    def test_manual_switch_rejects_foreign_region_node(self):
        self.sw.board = self.Board([
            {"node": "US-node", "id": "us01", "region": "us", "score": 80},
        ])
        self.sw.clash = self.Clash()
        self.assertFalse(self.sw.force_activate("eu", "US-node"))
        self.assertIsNone(self.sw.active_node("eu"))

    def test_partial_selector_chain_is_not_reported_as_success(self):
        self.sw.board = self.Board([
            {"node": "EU-node", "id": "eu01", "region": "eu", "score": 80},
        ])
        self.sw.clash = self.Clash(fail_on="prod")
        self.assertFalse(self.sw.force_activate("eu", "EU-node"))
        self.assertIsNone(self.sw.active_node("eu"))


if __name__ == "__main__":
    unittest.main()
