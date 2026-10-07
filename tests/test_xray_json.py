"""JSON-подписка Xray (массив конфигов клиента) → узлы sing-box (nodes_fetch.parsers.xray_json)."""

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_fetch.parsers import parse_link, xray_json  # noqa: E402
from nodes_fetch.sources import nodes_from_text  # noqa: E402

U = "b831381d-6324-4d53-ad4f-8cda48b30811"
PBK = "rWRM2cPptEBzNrJ0UGmTJ5yzE7jzDueFBG7N26IQ6Bw"
SERVICE = [{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}]


def vless_reality(host, sid, tag="proxy"):
    return {"tag": tag, "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": 443, "users": [
                {"id": U, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "serverName": "sni.example", "publicKey": PBK, "shortId": sid,
                "fingerprint": "firefox"}}}


def vless_xhttp(host):
    return {"tag": "proxy-wl", "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": 443,
                                    "users": [{"id": U, "encryption": "none", "flow": ""}]}]},
            "streamSettings": {"network": "xhttp", "xhttpSettings": {
                "mode": "packet-up", "host": "cdn.example", "path": "/live/",
                "extra": {"xPaddingBytes": "0-0", "scMaxEachPostBytes": 1000000,
                          "uplinkChunkSize": 0,
                          "xmux": {"maxConcurrency": "16-32"}}},
                "security": "tls", "tlsSettings": {"serverName": "front.example",
                                                   "fingerprint": "chrome", "alpn": ["h2"]}},
            "mux": {"enabled": True, "concurrency": 8}}


def config(remarks, *proxies):
    return {"remarks": remarks, "outbounds": [*proxies, *SERVICE]}


class Ctx:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


def parse(items):
    return nodes_from_text(json.dumps(items, ensure_ascii=False), Ctx())


class XrayJsonTest(unittest.TestCase):
    def test_vless_reality_matches_share_link(self):
        node = parse([config("Финляндия", vless_reality("a.example", "aa11"))])[0]
        link = (f"vless://{U}@a.example:443?security=reality&type=tcp&flow=xtls-rprx-vision"
                f"&sni=sni.example&pbk={PBK}&sid=aa11&fp=firefox#Финляндия")
        self.assertEqual(node, parse_link(link)[0])

    def test_duplicates_with_random_short_id_collapse_to_own_name(self):
        auto = config("Авто", vless_reality("a.example", "1111", "p1"),
                      vless_reality("b.example", "2222", "p2"))
        nodes = parse([auto, config("Финляндия", vless_reality("a.example", "3333")),
                       config("Швеция", vless_reality("b.example", "4444"))])
        self.assertEqual([(n["tag"], n["server"]) for n in nodes],
                         [("Финляндия", "a.example"), ("Швеция", "b.example")])
        self.assertEqual(nodes[0]["tls"]["reality"]["short_id"], "1111")   # первое вхождение

    def test_fallback_item_keeps_new_node_without_number(self):
        nodes = parse([config("Финляндия", vless_reality("a.example", "1")),
                       config("Германия (БС)", vless_reality("a.example", "2", "decoy"),
                              vless_xhttp("203.0.113.4"))])
        self.assertEqual([n["tag"] for n in nodes], ["Финляндия", "Германия (БС)"])
        node = nodes[1]
        self.assertNotIn("flow", node)
        self.assertNotIn("multiplex", node)                                # mux.cool не переносится
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False,
                                       "server_name": "front.example", "alpn": ["h2"],
                                       "utls": {"enabled": True, "fingerprint": "chrome"}})
        self.assertEqual(node["transport"], {
            "type": "xhttp", "path": "/live/", "host": "cdn.example", "mode": "packet-up",
            "x_padding_bytes": "0-0", "sc_max_each_post_bytes": "1000000-1000000",
            "xmux": {"max_concurrency": "16-32"}})

    def test_several_new_nodes_in_one_item_are_numbered(self):
        nodes = parse([config("Авто", vless_reality("a.example", "1", "p1"),
                              vless_reality("b.example", "2", "p2"))])
        self.assertEqual([n["tag"] for n in nodes], ["Авто 1", "Авто 2"])

    def test_name_from_narrowest_item(self):
        a, b = vless_reality("a.example", "1"), vless_reality("b.example", "1")
        c = vless_reality("c.example", "1")
        nodes = parse([config("Автовыбор", a, b, c), config("NL | Прямое", a, b),
                       config("NL | Запасное", a, c)])
        self.assertEqual([(n["tag"], n["server"]) for n in nodes],
                         [("NL | Прямое 1", "a.example"), ("NL | Прямое 2", "b.example"),
                          ("NL | Запасное", "c.example")])

    def test_hysteria_and_shadowsocks(self):
        hy = {"tag": "proxy", "protocol": "hysteria",
              "settings": {"address": "h.example", "port": 8449, "version": 2},
              "streamSettings": {"network": "hysteria", "hysteriaSettings": {"version": 2, "auth": "pw"},
                                 "security": "tls", "tlsSettings": {
                                     "serverName": "sni.example", "fingerprint": "firefox",
                                     "alpn": ["h3"]}}}
        ss = {"tag": "proxy", "protocol": "shadowsocks", "settings": {"servers": [
            {"address": "s.example", "port": 2030, "password": "pw",
             "method": "chacha20-ietf-poly1305", "uot": False}]},
            "streamSettings": {"network": "tcp", "security": "none"}}
        hy_node, ss_node = parse([config("GAMING", hy), config("SS", ss)])
        self.assertEqual(hy_node, {"tag": "GAMING", "type": "hysteria2", "server": "h.example",
                                   "server_port": 8449, "password": "pw",
                                   "tls": {"enabled": True, "insecure": False,
                                           "server_name": "sni.example", "alpn": ["h3"]}})
        self.assertEqual(ss_node, {"tag": "SS", "type": "shadowsocks", "server": "s.example",
                                   "server_port": 2030, "method": "chacha20-ietf-poly1305",
                                   "password": "pw"})

    def test_unsupported_outbound_is_logged_and_skipped(self):
        bad = vless_reality("a.example", "1")
        bad["streamSettings"]["network"] = "kcp"
        ctx = Ctx()
        nodes = nodes_from_text(json.dumps([config("KCP", bad),
                                            config("OK", vless_reality("b.example", "1"))]), ctx)
        self.assertEqual([n["tag"] for n in nodes], ["OK"])
        self.assertTrue(any("kcp" in line for line in ctx.lines))

    def test_singbox_json_is_not_xray(self):
        text = json.dumps({"outbounds": [{"type": "direct", "tag": "direct"}]})
        self.assertIsNone(xray_json.configs(text))
        self.assertIsNone(xray_json.configs("[1, 2]"))


if __name__ == "__main__":
    unittest.main()
