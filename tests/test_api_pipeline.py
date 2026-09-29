"""Offline authorization, allowlist and file editor API checks."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard import api_pipeline, api_singbox
from dashboard.webapp import App
from nodes_admin.orchestrator import Orchestrator


class PipelineApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / "config"
        self.config.mkdir()
        (self.config / "config.json").write_text("{}", encoding="utf-8")
        (self.config / "pipeline.json").write_text(json.dumps({
            "version": 1,
            "jobs": {"router": {"enabled": False, "times": []},
                     "clients": {"enabled": False, "times": []}},
            "timeout": 2,
        }), encoding="utf-8")
        nodes = root / "nodes.json"
        nodes.write_text("{}", encoding="utf-8")
        self.cfg = SimpleNamespace(path=str(self.config / "config.json"),
                                   storage=SimpleNamespace(nodes_file=str(nodes)))
        self.orch = Orchestrator(self.config, root / "data",
                                 command=[sys.executable, "-c", "import time; time.sleep(.3)"])
        self.addCleanup(self.orch.stop)
        self.app = App(self.cfg, runner=SimpleNamespace(orchestrator=self.orch), token="secret")
        api_pipeline.register(self.app)
        api_singbox.register(self.app)

    def request(self, method, path, data=None, *, token="secret", revision=None):
        headers = {}
        if token is not None:
            headers["X-Admin-Token"] = token
        if revision is not None:
            headers["If-Match"] = revision
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        response = self.app.handle(method, path, headers, body)
        return response.status, json.loads(response.body)

    def test_run_allowlist_auth_and_busy(self):
        self.assertEqual(self.request("POST", "/api/pipeline/run",
                                      {"mode": "router", "dry_run": True}, token=None)[0], 401)
        for body in ({"mode": "other", "dry_run": True},
                     {"mode": "router", "dry_run": False, "command": "whoami"}):
            self.assertEqual(self.request("POST", "/api/pipeline/run", body)[0], 400)
        status, data = self.request("POST", "/api/pipeline/run", {"mode": "router", "dry_run": True})
        self.assertEqual(status, 202)
        self.assertIn("run_id", data)
        self.assertEqual(self.request("POST", "/api/pipeline/run",
                                      {"mode": "clients", "dry_run": False})[0], 409)
        deadline = time.monotonic() + 5
        while self.orch.run(data["run_id"])["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.03)
        self.assertEqual(self.orch.run(data["run_id"])["status"], "ok")

    def test_file_paths_revision_and_base_check(self):
        for path in ("../secret.json", "%2E%2E%2Fsecret.json", "clients/other.json", "C:%5Ctemp"):
            self.assertEqual(self.request("PUT", "/api/singbox/files/" + path, {}, revision="missing")[0], 400)
        self.assertEqual(self.request("PUT", "/api/singbox/files/rules.json", {}, token=None,
                                      revision="missing")[0], 401)
        rules = {"version": 1, "rules": []}
        status, saved = self.request("PUT", "/api/singbox/files/rules.json", rules, revision="missing")
        self.assertEqual(status, 200)
        self.assertEqual(self.request("PUT", "/api/singbox/files/rules.json", rules,
                                      revision="missing")[0], 409)
        old = saved["revision"]
        self.assertEqual(self.request("PUT", "/api/singbox/files/rules.json", rules,
                                      revision=old)[0], 200)
        versions = self.request("GET", "/api/singbox/files/rules.json/history")[1]["versions"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.request("GET", "/api/singbox/files/rules.json/history?ts=" + versions[0])[1]["data"], rules)
        self.assertEqual(self.request("GET", "/api/singbox/files/rules.json/history?ts=..%2Fsecret")[0], 400)
        current = self.request("GET", "/api/singbox/files/rules.json")[1]["revision"]
        self.assertEqual(self.request("POST", "/api/singbox/files/rules.json/restore",
                                      {"ts": versions[0]}, revision="missing")[0], 409)
        self.assertEqual(self.request("POST", "/api/singbox/files/rules.json/restore",
                                      {"ts": versions[0]}, revision=current)[0], 200)

        base = self.config / "singbox/base.json"
        base.write_text('{"old":true}', encoding="utf-8")
        with patch("dashboard.api_singbox.subprocess.run") as runner:
            runner.return_value = SimpleNamespace(returncode=1, stdout="", stderr="bad config")
            status, _ = self.request("PUT", "/api/singbox/files/base.json", {"new": True},
                                     revision=self.request("GET", "/api/singbox/files/base.json")[1]["revision"])
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(base.read_text(encoding="utf-8")), {"old": True})


if __name__ == "__main__":
    unittest.main()
