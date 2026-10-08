"""Пресеты правил: проверка, склейка с базой и нодами, API админки, режим apply."""

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
        meta["priorities"] = {"dns.rules": dns_prio}
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

    def test_assemble_order_and_shared_tags(self):
        ru = _preset(300, route=[{"r": "ru"}], dns=[{"d": "ru"}], dns_prio=150)
        ru["route"]["rule_set"] = [{"tag": "geosite-ru"}]
        ru["dns"]["servers"] = [{"tag": "dns-ai"}]
        ai = _preset(200, route=[{"r": "ai1"}, {"r": "ai2"}], dns=[{"d": "ai"}])
        ai["dns"]["servers"] = [{"tag": "dns-ai"}]
        self._write("ru", ru)
        self._write("ai", ai)
        self._write("off", _preset(100, route=[{"r": "off"}], enabled=False))
        base = {"route": {"rules": [{"r": "base"}], "final": "global"}}
        nodes = {"outbounds": [{"tag": "n1"}]}
        config, warnings = presets.assemble(base, nodes, presets.load(self.dir))
        self.assertEqual(config["route"]["rules"],
                         [{"r": "base"}, {"r": "ai1"}, {"r": "ai2"}, {"r": "ru"}])
        self.assertEqual(config["dns"]["rules"], [{"d": "ru"}, {"d": "ai"}])
        self.assertEqual(config["dns"]["servers"], [{"tag": "dns-ai"}])
        self.assertEqual(config["outbounds"], [{"tag": "n1"}])
        self.assertNotIn("_preset", config)
        self.assertEqual(warnings, [])

    def test_validation(self):
        bad = [({"_preset": {"enabled": "yes"}}, "enabled"), ({"_preset": {"priority": 1.5}}, "priority"),
               ({"_preset": {"priorities": {"dns.rules": "1"}}}, "priorities"),
               ({"_preset": {"requires": {"users": []}}}, "requires"),
               ({"_preset": {"dns_priority": 150}}, "dns_priority")]
        for data, msg in bad:
            with self.assertRaisesRegex(presets.PresetError, msg):
                presets.validate("x", data)
        with self.assertRaises(presets.PresetError):
            presets.validate("../x", {})
        meta = presets.validate("x", {"inbounds": [], "route": {"final": "x"}})
        self.assertEqual((meta["enabled"], meta["priority"], meta["priorities"]), (False, 500, {}))

    def test_status_missing_groups(self):
        self._write("us", _preset(450, groups=["us"]))
        nodes = self.dir / "nodes.json"
        nodes.write_text(json.dumps({"outbounds": [{"tag": "eu-auto-out"}, {"tag": "x"}]}))
        st = presets.status(presets.load(self.dir), presets.node_groups(nodes))
        self.assertEqual(st[0]["missing_groups"], ["us"])
        self.assertIsNone(presets.status(presets.load(self.dir), None)[0]["missing_groups"])

    def test_cli_assemble(self):
        self._write("a", _preset(1, route=[{"r": 1}]))
        base, nodes, out = self.dir / "base.j", self.dir / "nodes.j", self.dir / "out.j"
        base.write_text('{"log": {}}', encoding="utf-8")
        nodes.write_text('{"outbounds": []}', encoding="utf-8")
        args = ["assemble", "--base", str(base), "--nodes", str(nodes), "--dir", str(self.dir), "--out", str(out)]
        with patch("sys.stderr.write"):
            self.assertEqual(presets.main(args), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         {"log": {}, "outbounds": [], "route": {"rules": [{"r": 1}]}})
        self._write("b", {"_preset": {"enabled": True}, "log": []})
        with patch("sys.stderr.write"):
            self.assertEqual(presets.main(args), 1)


LIBRARY = Path(__file__).resolve().parent.parent / "config" / "singbox" / "presets"
CLIENT_PRESETS = {"client-core", "android-no-package", "client-home-lan", "mode-whitelist",
                  "android-ai-apps", "android-browser-stun", "mode-abroad", "android-ru-apps"}


def _selector(tag, *members):
    return {"type": "selector", "tag": tag, "outbounds": list(members)}


def _rule_refs(rules):
    """Пары (поле, значение) ссылок правил на выходы, DNS-серверы и наборы правил."""
    for rule in rules:
        yield from _rule_refs(rule.get("rules", []))
        for key in ("outbound", "server"):
            if key in rule:
                yield key, rule[key]
        sets = rule.get("rule_set", [])
        for tag in [sets] if isinstance(sets, str) else sets:
            yield "rule_set", tag


class PresetLibraryTest(unittest.TestCase):
    """Библиотека config/singbox/presets: склеивается без конфликтов, ссылки разрешаются."""

    def assemble(self, names, base, nodes):
        library = presets.load(LIBRARY)
        for p in library:
            p["meta"]["enabled"] = p["name"] in names
        config, _ = presets.assemble(base, nodes, library)
        return config

    def assert_refs_resolved(self, config):
        defined = {
            "outbound": {o["tag"] for o in config["outbounds"]},
            "server": {s["tag"] for s in config["dns"]["servers"]},
            "rule_set": {r["tag"] for r in config["route"]["rule_set"]},
        }
        refs = list(_rule_refs(config["route"]["rules"] + config["dns"]["rules"]))
        refs += [("outbound", s["detour"]) for s in config["dns"]["servers"] if "detour" in s]
        refs += [("outbound", m) for o in config["outbounds"] for m in o.get("outbounds", [])]
        refs.append(("outbound", config["route"]["final"]))
        missing = sorted({(kind, tag) for kind, tag in refs if tag not in defined[kind]})
        self.assertEqual(missing, [])

    def test_all_disabled(self):
        library = presets.load(LIBRARY)
        self.assertTrue(library)
        self.assertEqual([p["name"] for p in library if p["meta"]["enabled"]], [])

    def test_router(self):
        names = {p["name"] for p in presets.load(LIBRARY)} - CLIENT_PRESETS - {"ai-google-smartdns"}
        base = {"dns": {"servers": [{"type": "local", "tag": "bootstrap"}], "final": "dns-global"},
                "outbounds": [{"type": "direct", "tag": "direct-out"}, {"type": "direct", "tag": "direct-lan"}]}
        nodes = {"outbounds": [_selector(t) for t in
                               ("global-auto-out", "ai-auto-out", "us-auto-out", "nodes-tester")]}
        config = self.assemble(names, base, nodes)
        self.assert_refs_resolved(config)
        self.assertEqual(config["route"]["final"], "global-auto-out")

    def test_client(self):
        names = CLIENT_PRESETS | {"block-ads", "ip-check", "ai-services", "ai-google-smartdns",
                                  "zone-us", "ru-direct", "geoip-ru"}
        base = {"dns": {"servers": [{"type": "https", "tag": "bootstrap", "server": "9.9.9.9"}]},
                "outbounds": [{"type": "direct", "tag": "direct-out"}] + [
                    _selector(t, "direct-out")
                    for t in ("global-out", "ai-out", "google-out", "us-out", "home-out")],
                "route": {"final": "global-out"}}
        config = self.assemble(names, base, {"outbounds": [_selector("global-auto-out")]})
        self.assert_refs_resolved(config)
        self.assertEqual(config["route"]["rules"][0]["package_name_regex"], [".*"])


def _fake_run(returncode=0, stderr=""):
    """subprocess.run для API: sing-box check с заданным результатом."""
    def run(cmd, **_):
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return run


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
            check = run.call_args_list[0].args[0]
            self.assertEqual(check[1:3], ["check", "-c"])
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

    def test_tag_conflict_rejected(self):
        (self.config / "singbox/base.json").write_text(
            '{"dns": {"servers": [{"tag": "bootstrap", "server": "9.9.9.9"}]}}', encoding="utf-8")
        dup = {"_preset": {"enabled": True}, "dns": {"servers": [{"tag": "bootstrap", "server": "1.1.1.1"}]}}
        with patch("dashboard.api_singbox.subprocess.run") as run:
            status, body = self.request("PUT", "/api/singbox/files/presets/x.json", dup, revision="missing")
            run.assert_not_called()                                       # до check не дошло
        self.assertEqual(status, 422)
        self.assertIn("тег bootstrap", body["error"])

    def test_invalid_preset_rejected_without_singbox(self):
        with patch("dashboard.api_singbox.subprocess.run") as run:
            status, _ = self.request("PUT", "/api/singbox/files/presets/x.json",
                                     {"_preset": {"enabled": "yes"}}, revision="missing")
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
