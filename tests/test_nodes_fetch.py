"""nodes_fetch: providers → raw_nodes.json (офлайн, сеть подменена).

Покрытие: нормализация providers, изоляция упавшей подписки, last-good (stale) и его
истечение, пустой ответ = сбой, --only переносит остальные подписки, guard min_ratio,
контракт raw (без tag/_-полей в outbound, cc_hint), CLI: коды выхода 0/1/2, dry-run, --json.
"""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_common import raw as rawfmt  # noqa: E402
from nodes_fetch import __main__ as cli  # noqa: E402
from nodes_fetch import fetch, util  # noqa: E402

U = "11111111-1111-4111-8111-111111111111"
LINKS_A = "\n".join([
    f"vless://{U}@10.0.0.1:443?security=tls&sni=a.example&type=ws&path=%2F#🇩🇪 Germany",
    "trojan://pw@10.0.0.2:443?sni=b.example#🇳🇱 Netherlands",
])
LINKS_B = "hysteria2://pw@10.0.0.3:8443?sni=h.example#🇫🇮 Finland"
AWG = """[Interface]
Address = 10.8.0.2/32
PrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MDA=
[Peer]
PublicKey = cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHViMDA=
AllowedIPs = 0.0.0.0/0
Endpoint = 10.0.2.1:51820
"""
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class _Resp:
    def __init__(self, text):
        self.status_code = 200
        self.content = text.encode("utf-8")
        self.text = text


class FakeNet:
    """url → текст ответа (base64 как у настоящих подписок) или None (сбой)."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, url, user_agent=None, timeout=None, proxies=None, headers=None):
        self.calls.append((url, user_agent))
        self.headers = headers
        body = self.table.get(url)
        return None if body is None else _Resp(base64.b64encode(body.encode()).decode())


def _providers(**fetch_cfg):
    return {"subscribes": [
        {"tag": "A", "url": "https://s.example/a", "user_agent": "curl"},
        {"tag": "B", "url": "https://s.example/b"},
        {"tag": "OFF", "url": "https://s.example/off", "enabled": False},
    ], "fetch": dict({"retries": 0}, **fetch_cfg)}


class FetchTestBase(unittest.TestCase):
    def setUp(self):
        self._old_get = util.http_get
        self.addCleanup(setattr, util, "http_get", self._old_get)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = []

    def net(self, table):
        fake = FakeNet(table)
        util.http_get = fake
        return fake

    def run_fetch(self, providers, previous=None, only=None, now=NOW):
        norm = fetch.load_providers(providers, log=self.logs.append)
        return fetch.run(norm, self.tmp.name, name="t", previous=previous, only=only,
                         now=now, log=self.logs.append)


class ProvidersTest(unittest.TestCase):
    def test_normalizes(self):
        p = fetch.load_providers({"subscribes": [
            {"tag": "A", "url": "https://x", "user_agent": "curl"},
            {"tag": "H", "url": "happ://crypt4/abc"},
            {"tag": "F", "type": "folder", "path": "awg"},
            {"tag": "L", "file": "subs.txt", "enabled": False}]})
        kinds = {s["tag"]: (s["kind"], s["enabled"]) for s in p["subscribes"]}
        self.assertEqual(kinds, {"A": ("url", True), "H": ("happ", True),
                                 "F": ("folder", True), "L": ("file", False)})
        self.assertEqual(p["subscribes"][0]["user_agent"], "curl")
        self.assertEqual(p["fetch"], fetch.FETCH_DEFAULTS)

    def test_rejects_bad(self):
        for bad in ({}, {"subscribes": [{"url": "x"}]},
                    {"subscribes": [{"tag": "A", "url": "x"}, {"tag": "A", "url": "y"}]},
                    {"subscribes": [{"tag": "A"}]},
                    {"subscribes": [], "fetch": {"min_ratio": 2}}):
            with self.subTest(bad=bad), self.assertRaises(fetch.ProvidersError):
                fetch.load_providers(bad, log=lambda m: None)


class RunTest(FetchTestBase):
    def test_contract_and_order(self):
        net = self.net({"https://s.example/a": LINKS_A, "https://s.example/b": LINKS_B})
        raw = self.run_fetch(_providers())
        rawfmt.validate(raw)
        self.assertEqual([n["provider"] for n in raw["nodes"]], ["A", "A", "B"])
        self.assertEqual(raw["nodes"][0]["title"], "🇩🇪 Germany")
        for n in raw["nodes"]:
            self.assertNotIn("tag", n["outbound"])
            self.assertFalse(any(k.startswith("_") for k in n["outbound"]))
        self.assertEqual([s["provider"] for s in raw["sources"]], ["A", "B"])   # OFF пропущен
        self.assertTrue(all(s["ok"] and not s["stale"] for s in raw["sources"]))
        self.assertEqual(raw["nodes_hash"], rawfmt.nodes_hash(raw["nodes"]))
        self.assertIn(("https://s.example/a", "curl"), net.calls)

    def test_failed_source_isolated_without_previous(self):
        self.net({"https://s.example/a": LINKS_A})           # B не отвечает
        raw = self.run_fetch(_providers())
        src = {s["provider"]: s for s in raw["sources"]}
        self.assertTrue(src["A"]["ok"])
        self.assertFalse(src["B"]["ok"])
        self.assertIsNone(src["B"]["last_ok_at"])
        self.assertIn("FetchError", src["B"]["error"])
        self.assertEqual(len(raw["nodes"]), 2)

    def test_empty_response_is_failure(self):
        self.net({"https://s.example/a": LINKS_A, "https://s.example/b": "garbage-without-links"})
        raw = self.run_fetch(_providers())
        self.assertFalse({s["provider"]: s for s in raw["sources"]}["B"]["ok"])

    def test_last_good_within_window_then_expires(self):
        self.net({"https://s.example/a": LINKS_A, "https://s.example/b": LINKS_B})
        first = self.run_fetch(_providers(stale_max_hours=48))
        self.net({"https://s.example/a": LINKS_A})           # B упала
        later = self.run_fetch(_providers(stale_max_hours=48), previous=first,
                               now=NOW + timedelta(hours=10))
        b = {s["provider"]: s for s in later["sources"]}["B"]
        self.assertTrue(b["stale"])
        self.assertFalse(b["ok"])
        self.assertEqual(b["count"], 1)
        self.assertEqual(b["last_ok_at"], first["sources"][1]["last_ok_at"])
        self.assertEqual(later["nodes"], first["nodes"])      # ноды B сохранены
        expired = self.run_fetch(_providers(stale_max_hours=48), previous=later,
                                 now=NOW + timedelta(hours=49))
        b = {s["provider"]: s for s in expired["sources"]}["B"]
        self.assertFalse(b["stale"])
        self.assertEqual(b["count"], 0)
        self.assertEqual([n["provider"] for n in expired["nodes"]], ["A", "A"])

    def test_only_carries_other_sources(self):
        self.net({"https://s.example/a": LINKS_A, "https://s.example/b": LINKS_B})
        first = self.run_fetch(_providers())
        net = self.net({"https://s.example/b": LINKS_B})
        again = self.run_fetch(_providers(), previous=first, only=["B"],
                               now=NOW + timedelta(hours=1))
        self.assertEqual([c[0] for c in net.calls], ["https://s.example/b"])
        self.assertEqual(again["nodes"], first["nodes"])
        a = {s["provider"]: s for s in again["sources"]}["A"]
        self.assertEqual(a, first["sources"][0])              # A перенесена как была

    def test_folder_cc_hint(self):
        os.makedirs(os.path.join(self.tmp.name, "awg"))
        with open(os.path.join(self.tmp.name, "awg", "pl.conf"), "w", encoding="utf-8") as f:
            f.write(AWG)
        raw = self.run_fetch({"subscribes": [{"tag": "AWG", "type": "folder", "path": "awg"}]})
        self.assertEqual(raw["nodes"][0]["cc_hint"], "pl")
        self.assertEqual(raw["nodes"][0]["outbound"]["type"], "wireguard")

    def test_guard(self):
        prev = {"nodes": [{}] * 10}
        fetch.check_guard({"nodes": [{}] * 5}, prev, 0.5)       # ровно 50% — ок
        with self.assertRaises(fetch.GuardError):
            fetch.check_guard({"nodes": [{}] * 4}, prev, 0.5)
        fetch.check_guard({"nodes": []}, None, 0.5)             # нет прошлого — не проверяем


class CliTest(FetchTestBase):
    def _cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def _write_providers(self, providers):
        path = os.path.join(self.tmp.name, "providers-main.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(providers, f)
        return path

    def test_write_guard_dryrun(self):
        prov = self._write_providers(_providers(min_ratio=0.9))
        out = os.path.join(self.tmp.name, "raw", "main.json")
        self.net({"https://s.example/a": LINKS_A, "https://s.example/b": LINKS_B})
        code, stdout, _ = self._cli("-p", prov, "-o", out, "--json")
        self.assertEqual(code, 0)
        summary = json.loads(stdout)
        self.assertTrue(summary["written"] and summary["changed"])
        self.assertEqual(summary["nodes"], 3)
        self.assertEqual(rawfmt.load(out)["providers"], "providers-main")
        before = _read(out)

        self.net({"https://s.example/b": LINKS_B})             # A упала, прошлых нод 3 → 1+2stale
        code, _, _ = self._cli("-p", prov, "-o", out)
        self.assertEqual(code, 0)                              # last-good удержал число нод

        self.net({})                                           # всё упало, но last-good свежий
        code, _, _ = self._cli("-p", prov, "-o", out, "--dry-run", "--json")
        self.assertEqual(code, 0)

        with open(out, "w", encoding="utf-8") as f:            # прошлый raw без last_ok → guard
            raw = json.loads(before)
            for s in raw["sources"]:
                s["last_ok_at"] = None
            f.write(json.dumps(raw))
        guarded = _read(out)
        code, stdout, err = self._cli("-p", prov, "-o", out, "--json")
        self.assertEqual(code, 2)
        self.assertIn("GUARD", err)
        self.assertEqual(_read(out), guarded)   # файл не тронут
        code, _, _ = self._cli("-p", prov, "-o", out, "--force")
        self.assertEqual(code, 0)
        self.assertEqual(rawfmt.load(out)["nodes"], [])

    def test_bad_providers_exit_1(self):
        prov = self._write_providers({"subscribes": [{"tag": "A"}]})
        code, stdout, _ = self._cli("-p", prov, "-o", os.path.join(self.tmp.name, "x.json"),
                                    "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(stdout)["ok"])


class SchemaContractTest(FetchTestBase):
    """Выход fetch и наши providers проходят JSON-схемы из schemas/."""

    def _schema(self, name):
        with open(os.path.join(_ROOT, "schemas", name), encoding="utf-8") as f:
            return json.load(f)

    def test_raw_matches_schema(self):
        import jsonschema
        self.net({"https://s.example/a": LINKS_A})           # A ок, B упала
        raw = self.run_fetch(_providers())
        jsonschema.validate(raw, self._schema("raw_nodes.schema.json"))
        bad = json.loads(json.dumps(raw))
        bad["nodes"][0]["outbound"]["tag"] = "x"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, self._schema("raw_nodes.schema.json"))

    def test_providers_match_schema(self):
        import jsonschema
        schema = self._schema("providers.schema.json")
        for d in ("config", os.path.join("tests", "fixtures", "golden", "synthetic", "config")):
            path = os.path.join(_ROOT, d, "providers.json")
            if os.path.exists(path):
                with self.subTest(providers=d), open(path, encoding="utf-8") as f:
                    jsonschema.validate(json.load(f), schema)


if __name__ == "__main__":
    unittest.main()
