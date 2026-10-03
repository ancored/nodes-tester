"""Пресеты правил: проверка, порядок фрагментов для sing-box merge, API админки, режим apply."""

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
from nodes_admin import presets
from nodes_admin.orchestrator import Orchestrator


def _preset(prio, route=(), dns=(), enabled=True, dns_prio=None, groups=(), **extra):
    meta = {"title": "t", "enabled": enabled, "priority": prio}
    if dns_prio is not None:
        meta["dns_priority"] = dns_prio
    if groups:
        meta["requires"] = {"groups": list(groups)}
    data = {"_preset": meta, **extra}
    if route:
        data.setdefault("route", {})["rules"] = list(route)
    if dns:
        data.setdefault("dns", {})["rules"] = list(dns)
    return data


class PresetModuleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def _write(self, name, data):
        (self.dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_order_route_by_priority_dns_by_dns_priority(self):
        ru = _preset(300, route=[{"r": "ru"}], dns=[{"d": "ru"}], dns_prio=150)
        ru["route"]["rule_set"] = [{"tag": "geosite-ru"}]
        ai = _preset(200, route=[{"r": "ai1"}, {"r": "ai2"}], dns=[{"d": "ai"}])
        ai["dns"]["servers"] = [{"tag": "dns-ai"}]
        self._write("ru", ru)
        self._write("ai", ai)
        self._write("off", _preset(100, route=[{"r": "off"}], enabled=False))
        frags = presets.fragments(presets.load(self.dir))
        route = [r for f in frags for r in f.get("route", {}).get("rules", [])]
        dns = [r for f in frags for r in f.get("dns", {}).get("rules", [])]
        self.assertEqual(route, [{"r": "ai1"}, {"r": "ai2"}, {"r": "ru"}])
        self.assertEqual(dns, [{"d": "ru"}, {"d": "ai"}])
        self.assertTrue(all("_preset" not in f for f in frags))
        servers = [s for f in frags for s in f.get("dns", {}).get("servers", [])]
        self.assertEqual(servers, [{"tag": "dns-ai"}])

    def test_validation(self):
        bad = [({"inbounds": []}, "недопустимые"), ({"route": {"final": "x"}}, "route.final"),
               ({"_preset": {"enabled": "yes"}}, "enabled"), ({"_preset": {"priority": 1.5}}, "priority"),
               ({"_preset": {"requires": {"users": []}}}, "requires")]
        for data, msg in bad:
            with self.assertRaisesRegex(presets.PresetError, msg):
                presets.validate("x", data)
        with self.assertRaises(presets.PresetError):
            presets.validate("../x", {})
        meta = presets.validate("x", {})
        self.assertEqual((meta["enabled"], meta["priority"], meta["dns_priority"]), (False, 500, 500))

    def test_status_missing_groups(self):
        self._write("us", _preset(450, groups=["us"]))
        nodes = self.dir / "nodes.json"
        nodes.write_text(json.dumps({"outbounds": [{"tag": "eu-auto-out"}, {"tag": "x"}]}))
        st = presets.status(presets.load(self.dir), presets.node_groups(nodes))
        self.assertEqual(st[0]["missing_groups"], ["us"])
        self.assertIsNone(presets.status(presets.load(self.dir), None)[0]["missing_groups"])

    def test_cli_fragments(self):
        self._write("a", _preset(1, route=[{"r": 1}]))
        out = self.dir / "out"
        with patch("sys.stdout.write") as write:
            self.assertEqual(presets.main(["fragments", "--dir", str(self.dir), "--out-dir", str(out)]), 0)
        paths = [c.args[0] for c in write.call_args_list if c.args[0].strip()]
        self.assertEqual(json.loads(Path(paths[0].strip()).read_text()), {"route": {"rules": [{"r": 1}]}})


def _fake_run(merged=None, returncode=0, stderr=""):
    """subprocess.run для API: merge пишет итоговый конфиг (по умолчанию пустой)."""
    def run(cmd, **_):
        if cmd[1] == "merge":
            Path(cmd[2]).write_text(json.dumps(merged or {}), encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return run


class DuplicateTagsTest(unittest.TestCase):
    def test_sections(self):
        cfg = {"outbounds": [{"tag": "d"}], "endpoints": [{"tag": "d"}],
               "dns": {"servers": [{"tag": "b"}, {"tag": "b"}, {"tag": "b"}, {"tag": "c"}]},
               "route": {"rule_set": [{"tag": "ru"}, {"tag": "ru"}], "rules": [{"r": 1}, {"r": 1}]},
               "inbounds": [{"tag": "tun-in"}, {}]}
        self.assertEqual(presets.duplicate_tags(cfg),
                         ["outbounds+endpoints: d", "dns.servers: b", "route.rule_set: ru"])
        self.assertEqual(presets.duplicate_tags({"inbounds": [{}, {}]}), [])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text(json.dumps({"dns": {"servers": [{"tag": "b"}, {"tag": "b"}]}}))
            with patch("sys.stderr.write"):
                self.assertEqual(presets.main(["check-tags", "--config", str(path)]), 1)
            path.write_text(json.dumps({"dns": {"servers": [{"tag": "b"}]}}))
            self.assertEqual(presets.main(["check-tags", "--config", str(path)]), 0)


class PresetApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / "config"
        (self.config / "singbox/presets").mkdir(parents=True)
        (self.config / "config.json").write_text("{}", encoding="utf-8")
        (self.config / "singbox/base.json").write_text('{"log": {}}', encoding="utf-8")
        (self.config / "pipeline.json").write_text(json.dumps({
            "version": 1, "jobs": {"router": {"enabled": False, "times": []},
                                   "clients": {"enabled": False, "times": []}}, "timeout": 2}))
        nodes = root / "nodes.json"
        nodes.write_text(json.dumps({"outbounds": [{"tag": "us-auto-out"}]}), encoding="utf-8")
        self.orch = Orchestrator(self.config, root / "data",
                                 command=[sys.executable, "-c", "import time; time.sleep(.2)"])
        self.addCleanup(self.orch.stop)
        cfg = SimpleNamespace(path=str(self.config / "config.json"),
                              storage=SimpleNamespace(nodes_file=str(nodes)))
        self.app = App(cfg, runner=SimpleNamespace(orchestrator=self.orch), token="secret")
        api_pipeline.register(self.app)
        api_singbox.register(self.app)

    def request(self, method, path, data=None, revision=None):
        headers = {"X-Admin-Token": "secret"}
        if revision is not None:
            headers["If-Match"] = revision
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        response = self.app.handle(method, path, headers, body)
        return response.status, json.loads(response.body)

    def test_save_checks_whole_assembly_and_lists(self):
        url = "/api/singbox/files/presets/us.json"
        with patch("dashboard.api_singbox.subprocess.run", side_effect=_fake_run()) as run:
            status, _ = self.request("PUT", url, _preset(450, route=[{"r": 1}], groups=["us"]),
                                     revision="missing")
            self.assertEqual(status, 200)
            merge = run.call_args_list[0].args[0]
            self.assertEqual(merge[1], "merge")
            self.assertEqual(sum(1 for a in merge if a == "-c"), 3)   # база, ноды, пресет
        status, data = self.request("GET", "/api/singbox/presets")
        self.assertEqual(status, 200)
        self.assertEqual(data["presets"][0]["missing_groups"], [])
        self.assertEqual(data["groups"], ["us"])

        with patch("dashboard.api_singbox.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="unknown outbound")
            rev = self.request("GET", url)[1]["revision"]
            status, body = self.request("PUT", url, _preset(450, route=[{"r": 2}]), revision=rev)
        self.assertEqual(status, 422)
        self.assertIn("unknown outbound", body["error"])
        saved = json.loads((self.config / "singbox/presets/us.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["route"]["rules"], [{"r": 1}])          # файл не перезаписан

        rev = self.request("GET", url)[1]["revision"]
        self.assertEqual(self.request("DELETE", "/api/singbox/files/base.json",
                                      revision="x")[0], 400)
        self.assertEqual(self.request("DELETE", url, revision=rev)[0], 200)
        self.assertFalse((self.config / "singbox/presets/us.json").exists())

    def test_duplicate_tags_rejected(self):
        dup = {"dns": {"servers": [{"tag": "bootstrap"}, {"tag": "bootstrap"}]}}
        with patch("dashboard.api_singbox.subprocess.run", side_effect=_fake_run(dup)) as run:
            status, body = self.request("PUT", "/api/singbox/files/presets/x.json",
                                        _preset(450), revision="missing")
            self.assertEqual(len(run.call_args_list), 1)                # до check не дошло
        self.assertEqual(status, 422)
        self.assertIn("dns.servers: bootstrap", body["error"])

    def test_invalid_preset_rejected_without_singbox(self):
        with patch("dashboard.api_singbox.subprocess.run") as run:
            status, _ = self.request("PUT", "/api/singbox/files/presets/x.json",
                                     {"inbounds": []}, revision="missing")
            run.assert_not_called()
        self.assertEqual(status, 422)

    def test_apply_mode_allowed_manually_only(self):
        status, data = self.request("POST", "/api/pipeline/run", {"mode": "apply", "dry_run": True})
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 5
        while self.orch.run(data["run_id"])["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.03)
        with self.assertRaises(ValueError):
            self.orch.start_run("apply", False, trigger="schedule")


if __name__ == "__main__":
    unittest.main()
