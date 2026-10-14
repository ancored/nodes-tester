"""Проверка «белый список»: IP сервера в подсетях и SNI в доменах; DoH без системного резолвера."""

import json
import os
import struct
import unittest
from unittest.mock import patch

import requests

from nodes_tester import whitelist
from nodes_tester.config import parse_config
from tests.helpers import make_runner, temp_dir

IP_TEXT = "203.0.113.0/24\n198.51.100.7/32\n2001:db8::/32\n"
SNI_TEXT = "vk.com\nyandex.ru\n"


class Resp:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content

    def raise_for_status(self):
        pass


def _answer(name, ips):
    """Ответ DNS: вопрос + A-записи со ссылкой на имя (0xC00C)."""
    query = whitelist._dns_query(name, 1)
    head = struct.pack(">HHHHHH", 0, 0x8180, 1, len(ips), 0, 0)
    body = query[12:]
    for ip in ips:
        body += struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4) + bytes(int(x) for x in ip.split("."))
    return head + body


class ListsTest(unittest.TestCase):
    def test_ip_and_suffix_match(self):
        lists = whitelist.Lists(IP_TEXT, SNI_TEXT)
        self.assertTrue(lists.ip_in("203.0.113.55"))
        self.assertTrue(lists.ip_in("198.51.100.7"))
        self.assertFalse(lists.ip_in("198.51.100.8"))
        self.assertTrue(lists.ip_in("2001:db8::1"))
        self.assertTrue(lists.sni_in("m.vk.com"))
        self.assertTrue(lists.sni_in("YANDEX.RU."))
        self.assertFalse(lists.sni_in("notvk.com"))

    def test_server_and_sni(self):
        self.assertEqual(whitelist.server_and_sni(
            {"type": "vless", "server": "1.2.3.4", "tls": {"enabled": True, "server_name": "vk.com"}}), ("1.2.3.4", "vk.com"))
        self.assertEqual(whitelist.server_and_sni(
            {"type": "trojan", "server": "n.example", "tls": {"enabled": True}}), ("n.example", "n.example"))
        self.assertEqual(whitelist.server_and_sni({"type": "shadowsocks", "server": "1.2.3.4"}), ("1.2.3.4", ""))
        self.assertEqual(whitelist.server_and_sni(
            {"type": "wireguard", "peers": [{"address": "5.6.7.8", "port": 51820}]}), ("5.6.7.8", ""))

    def test_dns_wire_roundtrip(self):
        self.assertEqual(whitelist._dns_addresses(_answer("n.example", ["203.0.113.9", "198.51.100.7"])),
                         ["203.0.113.9", "198.51.100.7"])

    def test_doh_urls_from_singbox_tag(self):
        path = os.path.join(temp_dir(), "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dns": {"servers": [
                {"type": "https", "tag": "dns-ru", "server": "dns.example", "path": "/abc/router"},
                {"type": "https", "tag": "dns-a", "server": "77.88.8.8"},
                {"type": "local", "tag": "dns-local"},
                {"type": "https", "tag": "dns-b", "server": "8.8.8.8", "server_port": 8443},
                {"type": "group", "tag": "dns-grp", "servers": ["dns-a", "dns-local", "dns-b"]}]}}, fh)
        self.assertEqual(whitelist.doh_urls("dns-ru", path), ["https://dns.example/abc/router"])
        self.assertEqual(whitelist.doh_urls("dns-grp", path),
                         ["https://77.88.8.8/dns-query", "https://8.8.8.8:8443/dns-query"])
        self.assertEqual(whitelist.doh_urls("dns-xx", path), ["https://1.1.1.1/dns-query"])
        self.assertEqual(whitelist.doh_urls("https://d/q", path), ["https://d/q"])

    def test_next_group_server_after_failure(self):
        path = os.path.join(temp_dir(), "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dns": {"servers": [
                {"type": "https", "tag": "a", "server": "192.0.2.1"},
                {"type": "https", "tag": "b", "server": "192.0.2.2"},
                {"type": "group", "tag": "dns-ru", "servers": ["a", "b"]}]}}, fh)
        check = whitelist.WhitelistCheck(parse_config({}).checks["whitelist"], path)
        with patch.object(whitelist, "resolve",
                          side_effect=[requests.HTTPError("505"), ["203.0.113.9"]]) as resolve:
            self.assertEqual(check._addresses("n.example"), ["203.0.113.9"])
        self.assertEqual([c.args[0] for c in resolve.call_args_list],
                         ["https://192.0.2.1/dns-query", "https://192.0.2.2/dns-query"])


class WhitelistCheckTest(unittest.TestCase):
    def setUp(self):
        params = parse_config({}).checks["whitelist"]
        self.calls = []

        def get(url, timeout):
            self.calls.append(url)
            return Resp(IP_TEXT if url.endswith("ip.txt") else SNI_TEXT)
        self.check = whitelist.WhitelistCheck(params, "/nonexistent", get=get)

    def test_verdicts(self):
        ok = {"type": "vless", "server": "203.0.113.5", "tls": {"enabled": True, "server_name": "vk.com"}}
        self.assertTrue(self.check.run(ok)["ok"])
        bad_sni = dict(ok, tls={"enabled": True, "server_name": "google.com"})
        self.assertFalse(self.check.run(bad_sni)["ok"])
        self.assertTrue(self.check.run({"type": "shadowsocks", "server": "198.51.100.7"})["ok"])   # без TLS — IP
        self.assertFalse(self.check.run({"type": "shadowsocks", "server": "192.0.2.1"})["ok"])
        self.assertEqual(len(self.calls), 2)                  # списки скачаны один раз

    def test_domain_resolved_by_doh_once(self):
        node = {"type": "vless", "server": "n.example", "tls": {"enabled": True, "server_name": "vk.com"}}
        with patch.object(whitelist, "resolve", return_value=["203.0.113.9"]) as resolve:
            res = self.check.run(node)
            self.check.run(node)
        self.assertTrue(res["ok"])
        self.assertEqual(res["ips"], ["203.0.113.9"])
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(resolve.call_args.args[0], "https://1.1.1.1/dns-query")   # тега dns-ru нет

    def test_unresolved_is_inconclusive(self):
        with patch.object(whitelist, "resolve", side_effect=ValueError("DNS rcode 3")):
            res = self.check.run({"type": "trojan", "server": "gone.example", "tls": {"enabled": True}})
        self.assertTrue(res["inconclusive"])


class RunnerWhitelistTest(unittest.TestCase):
    def test_pass_records_verdict_and_skips_fresh(self):
        tag = "P-vless-out [aaaa0001.00000001]"
        tmp = temp_dir()
        r = make_runner(tmp, tags=[tag])
        self.addCleanup(r.storage.close)
        del r._refresh_whitelist                              # настоящий метод, заглушку убрать
        with open(r.cfg.nodes_file, "w", encoding="utf-8") as fh:
            json.dump({"outbounds": [{"type": "shadowsocks", "tag": tag, "server": "198.51.100.7"}]}, fh)
        r.board.rows = {tag: {"node": tag, "id": "aaaa0001.00000001", "score": 50}}
        r._whitelist = whitelist.WhitelistCheck(r.cfg.checks["whitelist"], "/nonexistent",
                                                get=lambda url, timeout: Resp(IP_TEXT if url.endswith("ip.txt") else SNI_TEXT))
        nodes = r._enumerate_nodes()
        r._refresh_whitelist(nodes)
        self.assertEqual(r.board.rows[tag]["required"]["whitelist"]["ok"], "1")
        r._whitelist = None                                   # свежий результат — повторно не проверяем
        r._refresh_whitelist(nodes)
        self.assertIsNone(r._whitelist)


    def test_client_branch_nodes_checked_too(self):
        router, client = "P-vless-out [aaaa0001.00000001]", "W-vless-out [bbbb0002.00000002]"
        tmp = temp_dir()
        r = make_runner(tmp, tags=[router])
        self.addCleanup(r.storage.close)
        del r._refresh_whitelist
        for path, tag, ip in ((r.cfg.nodes_file, router, "198.51.100.7"), (r.cfg.whnodes_file, client, "192.0.2.1")):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"outbounds": [{"type": "shadowsocks", "tag": tag, "server": ip}]}, fh)
        r.board.rows = {router: {"node": router, "id": "aaaa0001.00000001", "score": 50}}
        r._whitelist = whitelist.WhitelistCheck(r.cfg.checks["whitelist"], "/nonexistent",
                                                get=lambda url, timeout: Resp(IP_TEXT if url.endswith("ip.txt") else SNI_TEXT))
        r._refresh_whitelist(r._enumerate_nodes())
        last = r.storage.last_result_ts("whitelist")
        self.assertEqual(set(last), {"aaaa0001.00000001", "bbbb0002.00000002"})
        self.assertNotIn(client, r.board.rows)                 # клиентская нода — не в рейтинге


if __name__ == "__main__":
    unittest.main()
