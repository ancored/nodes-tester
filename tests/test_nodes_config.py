"""nodes_config: raw + config.json → nodes.json роутера; nodes.json + кандидаты → whnodes.json.

Эквивалентность на записанных подписках проверяет test_golden; здесь — собственные
контракты стадии: фильтры с учётом причин, имена и ключи, группы из всех нод с запасными
членами, запись только при изменении, diff, коды выхода.
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
from nodes_config import build  # noqa: E402
from nodes_tester.config import parse_config  # noqa: E402

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
SUBS = [{"tag": "A", "url": "https://a", "exclude_names": ["promo"]}, {"tag": "B", "url": "https://b"}]


def _cfg(**sections):
    data = {"subscriptions": SUBS, "pipeline": {"router": {"exclude_types": ["shadowsocksr"]}}}
    data.update(sections)
    return parse_config(data)


def _build(raws=(RAW,), **sections):
    return build.build(list(raws), _cfg(**sections), log=lambda m: None)


def _leaves(frag):
    return [o for o in frag["outbounds"] if o["type"] not in ("selector", "urltest")]


class BuildTest(unittest.TestCase):
    def test_filters_names_and_report(self):
        frag, rep = _build()
        leaf = [o["tag"] for o in _leaves(frag)]
        # страны нет ни в имени, ни в фильтрах: RU-нода остаётся, решает тестер
        self.assertEqual(len(leaf), 3)
        for tag in leaf:
            self.assertRegex(tag, r"^[AB]-vless\|reality-out \[[0-9a-f]{8}\.[0-9a-f]{8}\]$")
        self.assertEqual(rep["dropped"], {"type": 1, "name": 1, "ungrouped_protocol": 1})
        self.assertTrue(all(o.get("domain_resolver") == "bootstrap" for o in _leaves(frag)))
        for o in frag["outbounds"]:
            self.assertFalse(any(k.startswith("_") for k in o))

    def test_router_addresses_dropped(self):
        wg = {"type": "wireguard", "address": ["10.8.0.2/32"], "private_key": "k",
              "peers": [{"address": "127.0.0.1", "port": 51820, "public_key": "p"}]}
        raw = _raw([("A", "zero", _vless("0.0.0.0")), ("A", "loop", _vless("::1")),
                    ("A", "host", _vless("localhost")), ("A", "wg", wg),
                    ("A", "lan", _vless("192.168.1.30")), ("A", "name", _vless("de.example.com"))])
        frag, rep = _build(raws=(raw,))
        self.assertEqual(len(_leaves(frag)), 2)
        self.assertEqual(rep["dropped"], {"local_address": 4})

    def test_groups_hold_every_node_with_fallbacks(self):
        frag, _ = _build(groups=[{"tag": "eu"}, {"tag": "ai", "strict": True, "in_global": False},
                                 {"tag": "us", "direct_fallback": True}])
        by_tag = {o["tag"]: o for o in frag["outbounds"]}
        nodes = [o["tag"] for o in _leaves(frag)]
        self.assertEqual(by_tag["eu-auto-out"]["outbounds"], ["failsafe-out"] + nodes)
        self.assertEqual(by_tag["eu-auto-out"]["default"], "failsafe-out")
        self.assertEqual(by_tag["ai-auto-out"]["outbounds"], ["block-out"] + nodes)
        self.assertEqual(by_tag["ai-auto-out"]["default"], "block-out")
        self.assertEqual(by_tag["us-auto-out"]["outbounds"][:2], ["failsafe-out", "direct-out"])
        self.assertEqual(by_tag["global-auto-out"]["outbounds"], ["eu-auto-out", "us-auto-out"])
        self.assertEqual(by_tag["nodes-tester"]["outbounds"], nodes + ["failsafe-out"])   # зонд монитора
        self.assertEqual(by_tag["failsafe-out"]["outbounds"], nodes)

    def test_exclude_subscriptions(self):
        frag, _ = _build(groups=[{"tag": "eu", "exclude_subscriptions": ["B"]},
                                 {"tag": "ai", "exclude_subscriptions": ["A"]}])
        by_tag = {o["tag"]: o for o in frag["outbounds"]}
        nodes = [o["tag"] for o in _leaves(frag)]
        a = [n for n in nodes if n.startswith("A-")]
        b = [n for n in nodes if n.startswith("B-")]
        self.assertEqual(by_tag["eu-auto-out"]["outbounds"], ["failsafe-out"] + a)
        self.assertEqual(by_tag["ai-auto-out"]["outbounds"], ["failsafe-out"] + b)
        self.assertEqual(by_tag["failsafe-out"]["outbounds"], nodes)    # не исключили все
        frag, _ = _build(groups=[{"tag": "eu", "exclude_subscriptions": ["B"]},
                                 {"tag": "ai", "exclude_subscriptions": ["B"]}])
        by_tag = {o["tag"]: o for o in frag["outbounds"]}
        self.assertEqual(by_tag["failsafe-out"]["outbounds"], a)
        self.assertEqual(by_tag["nodes-tester"]["outbounds"], a + ["failsafe-out"])
        self.assertEqual(len(_leaves(frag)), len(nodes))                # сами ноды остаются

    def test_exclude_unknown_subscription(self):
        from nodes_tester.config import ConfigError
        with self.assertRaisesRegex(ConfigError, "exclude_subscriptions.*'C'"):
            _cfg(groups=[{"tag": "eu", "exclude_subscriptions": ["C"]}])

    def test_strict_with_direct_fallback_defaults_to_direct(self):
        frag, _ = _build(groups=[{"tag": "ai", "strict": True, "direct_fallback": True}])
        ai = next(o for o in frag["outbounds"] if o["tag"] == "ai-auto-out")
        self.assertEqual(ai["outbounds"][:2], ["block-out", "direct-out"])
        self.assertEqual(ai["default"], "direct-out")
        self.assertFalse(any(o["tag"] == "failsafe-out" for o in frag["outbounds"]))   # только строгие

    def test_client_subscriptions_only_in_tester_selector(self):
        # Ноды подписок только для клиентов роутер проверяет, но трафик через них не пускает.
        frag, _ = build.build([RAW, _raw([("W", "x", _vless("10.0.1.1"))])],
                              _cfg(subscriptions=SUBS + [{"tag": "W", "url": "https://w", "branch": "clients"}],
                                   groups=[{"tag": "eu"}]), log=lambda m: None)
        by_tag = {o["tag"]: o for o in frag["outbounds"]}
        wh = [o["tag"] for o in _leaves(frag) if o["tag"].startswith("W-")]
        self.assertEqual(len(wh), 1)
        self.assertIn(wh[0], by_tag["nodes-tester"]["outbounds"])
        self.assertNotIn(wh[0], by_tag["eu-auto-out"]["outbounds"])
        self.assertNotIn(wh[0], by_tag["failsafe-out"]["outbounds"])

    def test_same_access_from_two_subscriptions_is_one_node(self):
        # Тот же сервер с тем же аккаунтом у двух провайдеров — один доступ, одна нода.
        node = _vless("10.0.0.7")
        frag, rep = _build([_raw([("A", "🇩🇪 x", node), ("B", "🇩🇪 y", dict(node))])])
        self.assertEqual(len(_leaves(frag)), 1)
        self.assertEqual(rep["dropped"]["duplicate"], 1)

    def test_new_credentials_keep_node_crc(self):
        # Провайдер перевыпустил uuid: меняется только вторая часть ключа.
        def key(uuid):
            frag, _ = _build([_raw([("A", "🇩🇪 x", dict(_vless("10.0.0.8"), uuid=uuid))])])
            tag = _leaves(frag)[0]["tag"]
            return tag[tag.index("[") + 1:-1].split(".")
        old, new = key("11111111-1111-1111-1111-111111111111"), key("22222222-2222-2222-2222-222222222222")
        self.assertEqual(old[0], new[0])
        self.assertNotEqual(old[1], new[1])

    def test_exclude_protocols_and_no_nodes(self):
        frag, rep = _build(pipeline={"router": {"exclude_types": ["shadowsocksr"],
                                                "exclude_protocols": ["reality"]}})
        self.assertEqual(_leaves(frag), [])
        self.assertEqual(rep["dropped"]["protocol"], 3)      # promo отброшена раньше по имени
        failsafe = next(o for o in frag["outbounds"] if o["tag"] == "failsafe-out")
        self.assertEqual(failsafe["outbounds"], ["direct-out"])      # urltest не бывает пустым


CLIENT_SUBS = SUBS + [{"tag": "W", "url": "https://w", "branch": "clients"},
                      {"tag": "X", "url": "https://x", "branch": "both"}]


def _client_setup(groups=None, **clients):
    """nodes.json роутера с нодами A, B (роутер), W (клиенты), X (обе) и Config."""
    raws = [RAW, _raw([("W", "w1", _vless("10.0.1.1")), ("W", "w2", _vless("10.0.1.2")),
                       ("X", "x1", _vless("10.0.2.1"))])]
    branch = {"domain_resolver": "dns-whitelist", **clients}
    if groups is not None:
        branch["groups"] = groups
    cfg = _cfg(subscriptions=CLIENT_SUBS, pipeline={"router": {"exclude_types": ["shadowsocksr"]},
                                                   "clients": branch})
    frag, _ = build.build(raws, cfg, log=lambda m: None)
    return frag, cfg, [o["tag"] for o in _leaves(frag)]


class ClientBuildTest(unittest.TestCase):
    def test_tested_client_nodes_in_groups(self):
        frag, cfg, tags = _client_setup([{"tag": "whitelist", "require": {"whitelist": {}}},
                                         {"tag": "spare", "in_global": False, "exclude_subscriptions": ["X"]}])
        (w1, w2), x1 = [t for t in tags if t.startswith("W-")], next(t for t in tags if t.startswith("X-"))
        a = next(t for t in tags if t.startswith("A-"))
        # Кандидат с роутерной подписки A в клиентов не попадает: у подписки ветвь router.
        out, rep = build.build_clients(frag, {"whitelist": [x1, w1, a], "spare": [w2, x1]}, cfg)
        by_tag = {o["tag"]: o for o in out["outbounds"]}
        self.assertEqual(by_tag["whitelist-auto-out"]["outbounds"], [w1, x1])     # порядок nodes.json
        self.assertEqual(by_tag["whitelist-auto-out"]["type"], "urltest")
        self.assertEqual(by_tag["spare-auto-out"]["outbounds"], [w2])
        self.assertEqual(by_tag["global-auto-out"]["outbounds"], ["whitelist-auto-out"])
        self.assertEqual([o["tag"] for o in _leaves(out)], [w1, w2, x1])
        self.assertTrue(all(o["domain_resolver"] == "dns-whitelist" for o in _leaves(out)))
        self.assertEqual(rep["groups"], {"whitelist": 2, "spare": 1})
        self.assertEqual(next(o for o in frag["outbounds"] if o["tag"] == w1)["domain_resolver"],
                         "bootstrap")                    # nodes.json роутера не тронут

    def test_client_filters_and_empty_group(self):
        frag, cfg, tags = _client_setup(exclude_protocols=["reality"])
        with self.assertRaisesRegex(ValueError, "группа all: нет нод"):
            build.build_clients(frag, {"all": tags}, cfg)
        frag, cfg, tags = _client_setup()
        with self.assertRaisesRegex(ValueError, "тестер не прислал"):
            build.build_clients(frag, {}, cfg)

    def test_fallback_always_in_group(self):
        # Запасной член из базы клиента (туннель домой) — последним; без нод группа из него одного.
        frag, cfg, tags = _client_setup([{"tag": "whitelist", "fallback": "global-router-auto-out"}])
        w1 = next(t for t in tags if t.startswith("W-"))
        out, rep = build.build_clients(frag, {"whitelist": [w1]}, cfg)
        by_tag = {o["tag"]: o for o in out["outbounds"]}
        self.assertEqual(by_tag["whitelist-auto-out"]["outbounds"], [w1, "global-router-auto-out"])
        out, rep = build.build_clients(frag, {"whitelist": []}, cfg)
        by_tag = {o["tag"]: o for o in out["outbounds"]}
        self.assertEqual(by_tag["whitelist-auto-out"]["outbounds"], ["global-router-auto-out"])
        self.assertEqual((_leaves(out), rep["groups"]), ([], {"whitelist": 0}))

    def test_detour_target_follows_node(self):
        frag, cfg, tags = _client_setup()
        w1, w2 = [t for t in tags if t.startswith("W-")]
        next(o for o in frag["outbounds"] if o["tag"] == w1)["detour"] = w2
        out, _ = build.build_clients(frag, {"all": [w1]}, cfg)
        self.assertEqual([o["tag"] for o in _leaves(out)], [w1, w2])
        self.assertEqual(next(o for o in out["outbounds"] if o["tag"] == "all-auto-out")["outbounds"], [w1])

    def test_client_group_checks_limited(self):
        from nodes_tester.config import ConfigError
        for groups, error in (([{"tag": "ai", "require": {"openai": {}}}], "openai: неизвестное поле"),
                              ([{"tag": "a", "in_global": False}], "in_global"),
                              ([{"tag": "a", "exclude_subscriptions": ["A"]}], "нет подписки ветви clients 'A'"),
                              ([{"tag": "a", "strict": True}], "strict: неизвестное поле")):
            with self.subTest(groups=groups), self.assertRaisesRegex(ConfigError, error):
                _client_setup(groups)

    def test_cli_candidates_age(self):
        frag, cfg, tags = _client_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts": 1000, "groups": {"all": tags}}, f)
            self.assertEqual(cli.load_candidates(path, now=1000 + 60), {"all": tags})
            with self.assertRaisesRegex(ValueError, "не обновляет"):
                cli.load_candidates(path, now=1000 + 3600)
            with self.assertRaisesRegex(ValueError, "тестер не запущен"):
                cli.load_candidates(os.path.join(tmp, "nope.json"))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = os.path.join(self.tmp.name, "raw.json")
        self._write(self.raw, RAW)
        self.out = os.path.join(self.tmp.name, "nodes.json")
        self.config = os.path.join(self.tmp.name, "config.json")
        self._write(self.config, {"subscriptions": SUBS})

    def _write(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["-c", self.config, *args])
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
        self._write(self.config, {"groups": [{"tag": "Bad Tag"}]})
        code, _ = self._cli("--raw", self.raw, "-o", self.out)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
