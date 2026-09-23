"""nodes_config: raw_nodes → nodes.json (+ groups_params v2, migrate v1→v2, CLI).

Эквивалентность прежнему subscribe проверяет test_golden (оба пути); здесь — собственные
контракты стадии: параметры, фильтры с учётом причин, запись только при изменении, diff,
коды выхода, миграция каталога.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_common import raw as rawfmt  # noqa: E402
from nodes_config import __main__ as cli  # noqa: E402
from nodes_config import build, migrate, params  # noqa: E402

PBK = "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyMDA"


def _vless(server, sid="aa"):
    return {"type": "vless", "server": server, "server_port": 443,
            "uuid": "11111111-1111-4111-8111-111111111111", "flow": "xtls-rprx-vision",
            "tls": {"enabled": True, "server_name": "www.example.com",
                    "reality": {"enabled": True, "public_key": PBK, "short_id": sid}}}


def _raw(nodes):
    items = [rawfmt.make_node(p, t, ob) for p, t, ob in nodes]
    return {"version": 1, "generated_at": "2026-09-23T00:00:00Z", "providers": "t",
            "nodes_hash": rawfmt.nodes_hash(items),
            "sources": [{"provider": p, "kind": "url", "ok": True, "count": 1,
                         "fetched_at": "x", "last_ok_at": "x", "stale": False, "error": None}
                        for p in dict.fromkeys(n[0] for n in nodes)],
            "nodes": items}


RAW = _raw([
    ("A", "🇩🇪 Germany ChatGPT", _vless("10.0.0.1")),
    ("A", "🇷🇺 Russia", _vless("10.0.0.2")),
    ("A", "🇳🇱 Netherlands promo", _vless("10.0.0.3")),
    ("B", "🇺🇸 USA", _vless("10.0.0.4")),
    ("B", "🇫🇮 Finland ssr", {"type": "shadowsocksr", "server": "10.0.0.5", "server_port": 1}),
    ("B", "🇸🇪 Sweden plain", {"type": "vless", "server": "10.0.0.6", "server_port": 80,
                               "uuid": "11111111-1111-4111-8111-111111111111"}),
])


class ParamsTest(unittest.TestCase):
    def test_defaults_and_v1(self):
        p = params.load(None)
        self.assertEqual(p["rename"]["domain_resolver_tag"], "bootstrap")
        self.assertFalse(p["raw_user_nodes"])
        v1 = params.load({"selector": {"interrupt_exist_connections": True},
                          "emit": {"nodes_tester": False}, "raw_user_nodes": True})
        self.assertEqual(v1["filters"], params.DEFAULTS["filters"])
        self.assertTrue(v1["raw_user_nodes"])
        self.assertFalse(v1["emit"]["nodes_tester"])

    def test_rejects_bad(self):
        for bad in ([], {"filters": {"exclude_types": "ssr"}},
                    {"filters": {"exclude_names": ["x"]}}, {"rename": {"labels": {"AI": "x"}}},
                    {"selector": []}, {"emit": {"ensure_regions": "eu"}}):
            with self.subTest(bad=bad), self.assertRaises(params.ParamsError):
                params.load(bad)


class MigrateTest(unittest.TestCase):
    def test_split_real_v1_config(self):
        with open(os.path.join(_ROOT, "config", "providers.json"), encoding="utf-8") as f:
            prov = json.load(f)
        with open(os.path.join(_ROOT, "config", "groups_params.json"), encoding="utf-8") as f:
            gp = json.load(f)
        prov2, gp2, output = migrate.split_v1(prov, gp)
        self.assertEqual(output, prov["save_config_path"])
        self.assertEqual(set(prov2), {"subscribes"})
        self.assertEqual([s["tag"] for s in prov2["subscribes"]], [s["tag"] for s in prov["subscribes"]])
        self.assertFalse(any("User-Agent" in s for s in prov2["subscribes"]))
        self.assertEqual(gp2["filters"]["exclude_countries"], prov["exclude_countries"])
        self.assertIn("shadowsocksr", gp2["filters"]["exclude_types"])
        self.assertEqual(gp2["rename"]["labels"], prov["labels"])
        self.assertEqual(gp2["selector"], gp["selector"])
        params.load(gp2)                                   # v2 валиден
        import jsonschema
        with open(os.path.join(_ROOT, "schemas", "groups_params.schema.json"), encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validate(gp2, schema)
        jsonschema.validate(gp, schema)                    # v1 тоже
        with open(os.path.join(_ROOT, "schemas", "providers.schema.json"), encoding="utf-8") as f:
            jsonschema.validate(prov2, json.load(f))
        again, gp3, _ = migrate.split_v1(prov2, gp2)       # идемпотентно
        self.assertEqual((again, gp3), (prov2, gp2))

    def test_ex_node_name_and_types(self):
        prov2, gp2, _ = migrate.split_v1({
            "subscribes": [{"tag": "A", "url": "x", "ex-node-name": "promo|test,", "emoji": True}],
            "exclude_protocol": "ssr, hy2"}, None)
        self.assertEqual(gp2["filters"]["exclude_names"], {"A": ["promo", "test"]})
        self.assertEqual(gp2["filters"]["exclude_types"], ["shadowsocksr", "hysteria2"])
        self.assertEqual(prov2["subscribes"], [{"tag": "A", "url": "x"}])

    def test_migrate_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(_ROOT, "tests", "fixtures", "golden", "synthetic", "config")
            dst = os.path.join(tmp, "v2")
            migrate.migrate_dir(src, dst, log=lambda m: None)
            self.assertTrue(os.path.isfile(os.path.join(dst, "awg", "pl.conf")))
            self.assertTrue(os.path.isfile(os.path.join(dst, "user_nodes.json")))
            with self.assertRaises(FileExistsError):
                migrate.migrate_dir(src, dst, log=lambda m: None)
            migrate.migrate_dir(src, dst, force=True, log=lambda m: None)
            with self.assertRaises(ValueError):
                migrate.migrate_dir(src, src, log=lambda m: None)


class BuildTest(unittest.TestCase):
    def _build(self, gp, user_nodes=()):
        return build.build([RAW], params.load(gp), user_nodes, log=lambda m: None)

    def test_filters_and_report(self):
        frag, rep = self._build({"filters": {"exclude_types": ["shadowsocksr"],
                                             "exclude_countries": ["ru"],
                                             "exclude_names": {"A": ["promo"], "*": ["nothing"]}},
                                 "rename": {"labels": {"AI": ["chatgpt"]}}})
        leaf = [o["tag"] for o in frag["outbounds"] if o["type"] == "vless"]
        self.assertEqual(len(leaf), 2)
        self.assertTrue(leaf[0].startswith("A-vless|reality-de-out [AI] ["))
        self.assertTrue(leaf[1].startswith("B-vless|reality-us-out ["))
        self.assertEqual(rep["dropped"], {"type": 1, "name": 1, "ungrouped_protocol": 1, "country": 1})
        self.assertEqual(rep["regions"], {"eu": 1, "us": 1})
        self.assertTrue(all(o.get("domain_resolver") == "bootstrap"
                            for o in frag["outbounds"] if o["type"] == "vless"))
        for o in frag["outbounds"]:
            self.assertFalse(any(k.startswith("_") for k in o))

    def test_same_raw_twice_is_deduped(self):
        frag, rep = build.build([RAW, RAW], params.load(None), (), log=lambda m: None)
        once, _ = build.build([RAW], params.load(None), (), log=lambda m: None)
        self.assertEqual(frag, once)
        self.assertEqual(rep["dropped"]["duplicate"], 5)

    def test_raw_user_nodes_verbatim(self):
        user = [dict(_vless("10.9.9.9"), tag="Custom-NL")]
        frag, _ = self._build({"raw_user_nodes": True}, user)
        self.assertIn("Custom-NL", build.tags(frag))
        frag, _ = self._build({}, user)
        self.assertTrue(any(t.startswith("Custom-vless|reality-nl-out [") for t in build.tags(frag)))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = os.path.join(self.tmp.name, "raw.json")
        self._write(self.raw, RAW)
        self.out = os.path.join(self.tmp.name, "nodes.json")

    def _write(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, (json.loads(out.getvalue()) if out.getvalue() else None)

    def test_write_only_on_change_and_diff(self):
        code, s = self._cli("--raw", self.raw, "-o", self.out, "--json")
        self.assertEqual(code, 0)
        self.assertTrue(s["changed"] and s["written"])
        self.assertEqual(s["removed"], [])
        mtime = os.stat(self.out).st_mtime_ns
        code, s = self._cli("--raw", self.raw, "-o", self.out, "--json")
        self.assertFalse(s["changed"] or s["written"])
        self.assertEqual(s["added"], [])
        self.assertEqual(os.stat(self.out).st_mtime_ns, mtime)      # файл не трогали

        smaller = dict(RAW, nodes=RAW["nodes"][:1])
        self._write(self.raw, smaller)
        code, s = self._cli("--raw", self.raw, "-o", self.out, "--json", "--check")
        self.assertTrue(s["changed"])
        self.assertFalse(s["written"])
        self.assertEqual(os.stat(self.out).st_mtime_ns, mtime)      # --check не пишет
        self.assertTrue(any(t.startswith("B-vless") for t in s["removed"]))
        self.assertEqual(s["added"], [])

    def test_errors_exit_1(self):
        code, s = self._cli("--raw", os.path.join(self.tmp.name, "nope.json"), "-o", self.out, "--json")
        self.assertEqual(code, 1)
        self.assertFalse(s["ok"])
        self._write(self.raw, dict(RAW, version=2))
        code, _ = self._cli("--raw", self.raw, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
