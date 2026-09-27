"""UX contracts; all storage/configs are temporary, Clash and network are fake."""
import json
import os
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from dashboard.data import collect
from dashboard.server import build_app
from nodes_tester.config import load_config
from tests.helpers import make_config, make_runner, temp_dir, fresh_nodes_json


class SnapshotHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_config(make_config(self.tmp, dashboard={"token": "demo"}))

    def test_missing_database_is_not_empty_success(self):
        data = collect(self.cfg)
        self.assertEqual(data["source"]["state"], "missing")
        self.assertEqual(data["nodes"], [])
        self.assertFalse(os.path.exists(self.cfg.storage.db_file))

    def test_corrupt_database_and_missing_schema_report_error(self):
        with open(self.cfg.storage.db_file, "wb") as fh:
            fh.write(b"not sqlite")
        app = build_app(self.cfg)
        self.assertEqual(app.handle("GET", "/api/data", {}, b"").status, 500)
        os.unlink(self.cfg.storage.db_file)
        sqlite3.connect(self.cfg.storage.db_file).close()
        self.assertEqual(app.handle("GET", "/api/data", {}, b"").status, 500)

    def test_disabled_storage_does_not_read_existing_file(self):
        with open(self.cfg.storage.db_file, "wb") as fh:
            fh.write(b"not sqlite")
        self.cfg.storage.enabled = False
        self.assertEqual(collect(self.cfg)["source"]["state"], "disabled")

    def test_empty_database_is_known_empty_with_health_in_slices(self):
        r = make_runner(self.tmp)
        try:
            data = collect(r.cfg)
            self.assertEqual(data["source"]["state"], "ok")
            self.assertIsNone(data["source"]["last_measurement"])
            self.assertEqual(data["nodes"], [])
            app = build_app(r.cfg)
            body = json.loads(app.handle("GET", "/api/rating", {}, b"").body)
            self.assertEqual(body["source"]["state"], "ok")
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertTrue(all(d["source"]["state"] == "ok" for d in pool.map(collect, [r.cfg]*12)))
        finally:
            r.storage.close()


class AdminContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.runner = make_runner(self.tmp, dashboard={"token": "demo", "read_open": False})
        self.app = build_app(self.runner.cfg, runner=self.runner)

    def tearDown(self):
        self.runner.storage.close()

    def call(self, method, path, body=None, **headers):
        return self.app.handle(method, path, {"X-Admin-Token": "demo", **headers},
                               json.dumps(body).encode() if body is not None else b"")

    def test_bootstrap_does_not_expose_secrets_and_session_is_validated(self):
        response = self.app.handle("GET", "/api/capabilities", {}, b"")
        self.assertEqual(response.status, 200)
        caps = json.loads(response.body)
        self.assertTrue(caps["run_pass"])
        self.assertFalse(caps["pipeline"])
        self.assertNotIn("token", caps)
        self.assertEqual(self.app.handle("GET", "/api/session", {}, b"").status, 401)
        self.assertEqual(self.app.handle("GET", "/api/data", {}, b"").status, 401)
        self.assertEqual(self.call("GET", "/api/session").status, 200)

    def test_standalone_can_edit_files_but_not_control_runner(self):
        app = build_app(self.runner.cfg)
        caps = json.loads(app.handle("GET", "/api/capabilities", {}, b"").body)
        self.assertTrue(caps["edit_config"])
        self.assertFalse(caps["node_actions"])
        self.assertFalse(caps["run_pass"])
        self.assertEqual(app.handle("POST", "/api/run/pass", {"X-Admin-Token":"demo"}, b"{}").status, 409)

    def test_revision_prevents_lost_updates_and_restart_is_visible(self):
        opened = json.loads(self.call("GET", "/api/config").body)
        changed = opened["data"]
        changed["report"]["console"] = not changed["report"]["console"]
        result = self.call("PUT", "/api/config", changed, **{"If-Match": opened["revision"]})
        self.assertEqual(result.status, 200)
        self.assertTrue(json.loads(result.body)["restart_required"])
        self.assertEqual(self.call("PUT", "/api/config", opened["data"], **{"If-Match":opened["revision"]}).status, 409)
        self.assertTrue(json.loads(self.call("GET", "/api/config").body)["restart_required"])

    def test_subscription_save_does_not_claim_application_or_drop_custom_fields(self):
        body = {"subscribes":[{"tag":"DEMO", "url":"https://example.invalid", "User-Agent":"curl", "custom":42}], "custom_top":True}
        result = self.call("PUT", "/api/config/providers", body)
        self.assertEqual(result.status, 200)
        self.assertEqual(json.loads(result.body)["application_state"], "not_applied_by_dashboard")
        loaded = json.loads(self.call("GET", "/api/config/providers").body)
        self.assertEqual(loaded["data"], body)

    def test_subscription_fetch_null_number_is_rejected(self):
        body = {"subscribes":[{"tag":"DEMO","url":"https://example.invalid"}], "fetch":{"timeout":None}}
        self.assertEqual(self.call("PUT", "/api/config/providers", body).status, 400)
        body["fetch"] = {"proxy": None, "timeout": 30}
        self.assertEqual(self.call("PUT", "/api/config/providers", body).status, 200)

    def test_only_active_restrictions_block_manual_choice(self):
        storage = self.runner.storage
        self.runner._pass_seq = 10
        storage.set_backoff("aaaa0001", None, 2, "backoff", until_pass=9)      # пауза истекла
        storage.set_backoff("aaaa0002", None, 2, "backoff", until_pass=11)     # пауза идёт
        storage.set_backoff("aaaa0003", 1, 7, "garbage")                        # карантин истёк
        storage.set_backoff("aaaa0004", 2**40, 7, "garbage")                    # карантин идёт
        self.assertEqual(self.runner.restricted_crcs(), {"aaaa0002", "aaaa0004"})

    def test_request_is_deduplicated_and_status_has_real_progress(self):
        self.runner._running = True
        a = json.loads(self.call("POST", "/api/run/pass").body)
        b = json.loads(self.call("POST", "/api/run/pass").body)
        self.assertFalse(a["already_queued"])
        self.assertTrue(b["already_queued"])
        status = json.loads(self.call("GET", "/api/status").body)
        self.assertTrue(status["request_queued"])
        self.assertEqual(status["progress"]["phase"], "stopped")
        self.assertTrue(self.runner._wait_interruptible(0))
        self.assertFalse(self.runner.status()["request_queued"])

    def test_stopped_runner_rejects_pass_request(self):
        self.assertEqual(self.call("POST", "/api/run/pass").status, 409)
        self.assertFalse(self.runner.status()["request_queued"])

    def test_banned_node_cannot_be_manually_activated_even_with_stale_score(self):
        tag = "DEMO-vless-nl-out [abcd1234]"
        path = fresh_nodes_json(self.tmp, [{"tag":tag,"type":"vless","server":"example.invalid","server_port":443}])
        self.runner.storage.load_nodes(path)
        self.runner.storage.set_banned("abcd1234", True)
        self.assertEqual(self.call("POST", "/api/regions/eu/switch", {"node":tag}).status, 409)


class RunnerProgressTest(unittest.TestCase):
    def test_progress_tracks_each_node_without_running_a_test(self):
        tmp = temp_dir()
        tags = ["DEMO-vless-nl-out [abcd1234]", "DEMO-vless-nl-out [abcd5678]"]
        r = make_runner(tmp, tags=tags, scoring={"enabled":False}, switching={"enabled":False})
        r._tests_cache = {}
        seen = []
        def fake_test(region, ident, pass_no, tests, params):
            seen.append(dict(r.status()["progress"]))
            return {"round":pass_no,"id":ident.node_id,"tests":{}}
        try:
            with patch.object(r, "_test_node", side_effect=fake_test), patch.object(r, "_respect_host_gap"):
                self.assertFalse(r._run_pass(1))
            self.assertEqual([s["processed"] for s in seen], [0,1])
            self.assertTrue(all(s["total"] == 2 and s["phase"] == "testing" for s in seen))
            self.assertEqual(r.status()["progress"]["processed"], 2)
        finally:
            r.storage.close()

    def test_pass_loads_bans_before_enumerating_and_keeps_presence(self):
        tmp = temp_dir()
        tag = "DEMO-vless-nl-out [abcd1234]"
        r = make_runner(tmp, tags=[tag])
        try:
            path = fresh_nodes_json(tmp, [{"tag":tag,"type":"vless","server":"example.invalid","server_port":443}])
            r.storage.load_nodes(path)
            r.storage.set_banned("abcd1234", True)
            with patch.object(r, "_test_node", side_effect=AssertionError("No network tests allowed")):
                self.assertTrue(r._run_pass(1))
            self.assertIn("abcd1234", r._banned)
            self.assertEqual(r.storage._db.execute("SELECT present FROM nodes WHERE crc='abcd1234'").fetchone()[0], 1)
        finally:
            r.storage.close()

    def test_no_storage_empty_pass_does_not_dereference_none(self):
        r = make_runner(temp_dir(), storage={"enabled":False}, scoring={"enabled":False}, switching={"enabled":False})
        self.assertTrue(r._run_pass(1))


if __name__ == "__main__":
    unittest.main()
