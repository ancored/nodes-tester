"""Идентичность ноды: ключ node_crc.cred_crc, наследование при новых учётных данных,
резерв по разным серверам, новая схема БД и измеренная страна."""

import json
import os
import sqlite3
import unittest

from naming import NODE_KEY_RE, node_key, parse_node, split_payload
from nodes_tester.config import load_config
from nodes_tester.scoreboard import Scoreboard
from nodes_tester.storage import SCHEMA_VERSION, Storage
from tests.helpers import fresh_nodes_json, make_config, temp_dir
from tests.test_reserve import _Base, _row

REALITY = {"enabled": True, "server_name": "sni.example", "reality": {"enabled": True, "public_key": "PBK", "short_id": "ab"}}


class CredentialSplitTest(unittest.TestCase):
    def assertSplit(self, node, creds, kept):
        settings, got = split_payload(node)
        self.assertEqual(got, creds)
        for path in kept:                       # поля сервера остаются в настройках
            value = settings
            for step in path:
                value = value[step]
            self.assertIsNotNone(value)

    def test_vless_uuid_and_reality_short_id(self):
        node = {"type": "vless", "server": "h", "server_port": 443, "uuid": "U", "flow": "xtls-rprx-vision", "tls": REALITY}
        self.assertSplit(node, {"uuid": "U", "tls.reality.short_id": "ab"},
                         [("flow",), ("tls", "reality", "public_key"), ("tls", "server_name")])

    def test_hysteria2_obfs_password_belongs_to_server(self):
        node = {"type": "hysteria2", "server": "h", "server_port": 443, "password": "P",
                "obfs": {"type": "salamander", "password": "SERVER"}}
        self.assertSplit(node, {"password": "P"}, [("obfs", "password")])

    def test_shadowsocks_method_stays(self):
        node = {"type": "shadowsocks", "server": "h", "server_port": 8388, "method": "aes-256-gcm", "password": "P"}
        self.assertSplit(node, {"password": "P"}, [("method",)])

    def test_tuic_uuid_and_password(self):
        node = {"type": "tuic", "server": "h", "server_port": 443, "uuid": "U", "password": "P"}
        self.assertSplit(node, {"uuid": "U", "password": "P"}, [])

    def test_wireguard_client_address_and_peer_psk_but_not_peer_address(self):
        node = {"type": "wireguard", "private_key": "K", "address": ["10.0.0.2/32"],
                "peers": [{"address": "1.2.3.4", "port": 51820, "public_key": "PUB", "pre_shared_key": "PSK"}]}
        self.assertSplit(node, {"private_key": "K", "address": ["10.0.0.2/32"], "peers.0.pre_shared_key": "PSK"},
                         [("peers", 0, "address"), ("peers", 0, "public_key")])

    def test_key_format_and_tag_parse(self):
        node = {"type": "trojan", "server": "h", "server_port": 443, "password": "P"}
        key = node_key(node)
        self.assertTrue(NODE_KEY_RE.fullmatch(key))
        ident = parse_node(f"hynet-ABC12-trojan|tls-out [{key}]")
        self.assertEqual((ident.provider, ident.protocol, ident.node_id), ("hynet-ABC12", "trojan|tls", key))
        self.assertEqual(f"{ident.node_crc}.{ident.cred_crc}", key)

    def test_service_fields_do_not_change_key(self):
        node = {"type": "trojan", "server": "h", "server_port": 443, "password": "P"}
        self.assertEqual(node_key(node), node_key({**node, "tag": "x", "domain_resolver": "b", "_provider": "A"}))


class InheritanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        cfg = load_config(make_config(self.tmp))
        self.storage = Storage(cfg.storage)
        self.board = Scoreboard(self.storage, cfg.rating)

    def tearDown(self):
        self.storage.close()

    def test_new_credentials_inherit_node_state_but_not_activity(self):
        old = parse_node("A-vless|reality-out [aaaa0001.11111111]")
        new = parse_node("A-vless|reality-out [aaaa0001.22222222]")
        tests = {"connectivity": {"ok": True, "exit_ip": "1.2.3.4", "country": "NL"},
                 "latency": {"ok": True, "ttfb_ms": 100}}
        for _ in range(3):
            self.board.record(old, "eu", tests)
        self.board.rows[old.raw]["active"] = 1
        self.board.set_required_result(old.raw, "gemini", True, "NLD")
        self.assertEqual(self.board.country(new), "nl")          # до первого замера — от ноды
        self.board.record(new, "eu", {"connectivity": {"ok": False}})
        row = self.board.get(new.raw)
        self.assertEqual(row["samples"], self.board.get(old.raw)["samples"] + 1)
        self.assertEqual(row["required"]["gemini"]["ok"], "1")    # проверка AI — у ноды
        self.assertEqual(row["exit_cc"], "NL")
        self.assertEqual(row["active"], 0)                        # активность — у доступа

    def test_other_server_does_not_inherit(self):
        self.board.record(parse_node("A-x-out [aaaa0001.11111111]"), "eu",
                          {"connectivity": {"ok": True, "exit_ip": "1.2.3.4", "country": "NL"}})
        self.assertEqual(self.board.country(parse_node("A-x-out [bbbb0001.11111111]")), "")


class StorageSchemaTest(unittest.TestCase):
    def test_other_schema_set_aside_and_history_starts_over(self):
        tmp = temp_dir()
        db = os.path.join(tmp, "stats.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE nodes (crc TEXT PRIMARY KEY, label TEXT)")
        con.execute("INSERT INTO nodes VALUES ('aaaa0001', 'AI')")
        con.commit()
        con.close()
        storage = Storage(load_config(make_config(tmp)).storage)
        try:
            self.assertFalse(storage.node_exists("aaaa0001"))
            self.assertEqual(storage.get_meta("schema"), SCHEMA_VERSION)
        finally:
            storage.close()
        self.assertTrue(any(name.startswith("stats.db.v1-") for name in os.listdir(tmp)))

    def test_connectivity_country_marks_every_access_of_the_node(self):
        tmp = temp_dir()
        storage = Storage(load_config(make_config(tmp)).storage)
        try:
            nodes = [{"tag": f"A-trojan-out [{node_key(n)}]", **n} for n in (
                {"type": "trojan", "server": "h", "server_port": 443, "password": "one"},
                {"type": "trojan", "server": "h", "server_port": 443, "password": "two"})]
            storage.load_nodes(fresh_nodes_json(tmp, nodes))
            first = parse_node(nodes[0]["tag"]).node_id
            storage.add_results({"id": first, "round": 1,
                                 "tests": {"connectivity": {"ok": True, "country": "DE", "exit_ip": "1.1.1.1"}}})
            rows = storage._db.execute("SELECT crc, node_crc, country, crc_ok FROM nodes ORDER BY crc").fetchall()
            self.assertEqual(len({r[1] for r in rows}), 1)          # один сервер
            self.assertEqual([r[2] for r in rows], ["de", "de"])
            self.assertEqual([r[3] for r in rows], [1, 1])
        finally:
            storage.close()


class ReserveDiversityTest(_Base):
    def test_one_access_per_server_and_not_the_active_server(self):
        r = self.r
        twin_a = "DEMO-vless-out [aaaa0001.00000002]"     # второй доступ сервера активной A
        twin_b = "DEMO-vless-out [aaaa0002.00000002]"     # второй доступ сервера B
        for tag, score in ((twin_a, 94), (twin_b, 89)):
            r.board.rows[tag] = {**_row(tag, score=score), "id": parse_node(tag).node_id}
        r.board.set_groups({"eu": set(r.board.rows) - {"DEMO-vless-us-out [bbbb0001]"}})
        r.reserve.maintain("eu")
        reserve = r.board.reserve("eu")
        self.assertNotIn(twin_a, reserve)
        self.assertEqual(len({parse_node(n).node_crc for n in reserve}), len(reserve))


if __name__ == "__main__":
    unittest.main()
