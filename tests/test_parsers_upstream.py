"""Исправления парсеров, перенесённые из upstream Toperlock/sing-box-subscribe
(коммиты после ede9fc4, до 558731c от 2026-09-23), и наши отступления от них.

Отступления: packet_encoding=xudp по умолчанию оставлен (поле входит в CRC —
см. test_crc_stable_*), xhttp в vless поддержан (upstream его отбрасывает),
encryption из Clash подставляется в единственный encryption= ссылки.
"""

import base64
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_fetch.parsers import vless, vmess, trojan, hysteria2, socks, clash2base64  # noqa: E402
from naming.crc import content_crc32  # noqa: E402


def _b64(s):
    return base64.b64encode(s.encode()).decode()


class VmessTest(unittest.TestCase):
    ITEM = {"v": "2", "ps": "ps-name", "add": "a.com", "port": "443", "id": "u-1",
            "net": "ws", "host": "h.com", "path": "/p", "tls": "tls", "sni": "h.com"}

    def test_fragment_name(self):
        # vmess://BASE64#имя — раньше фрагмент ломал b64-декод и нода терялась
        node = vmess.parse("vmess://" + _b64(json.dumps(self.ITEM)) + "#%F0%9F%87%A9%F0%9F%87%AA%20DE")
        self.assertIsNotNone(node)
        self.assertEqual(node["tag"], "🇩🇪 DE")

    def test_no_fragment_uses_ps(self):
        node = vmess.parse("vmess://" + _b64(json.dumps(self.ITEM)))
        self.assertEqual(node["tag"], "ps-name")

    def test_packet_encoding(self):
        self.assertEqual(vmess.parse("vmess://" + _b64(json.dumps(self.ITEM)))["packet_encoding"], "xudp")
        item = dict(self.ITEM, packetEncoding="packetaddr")
        self.assertEqual(vmess.parse("vmess://" + _b64(json.dumps(item)))["packet_encoding"], "packetaddr")
        item = dict(self.ITEM, packetEncoding="none")
        self.assertNotIn("packet_encoding", vmess.parse("vmess://" + _b64(json.dumps(item))))

    def test_crc_stable_default(self):
        # Отпечаток до переноса правок — xudp по умолчанию сохраняет теги нод
        self.assertEqual(content_crc32(vmess.parse("vmess://" + _b64(json.dumps(self.ITEM)))), "d9975681")


class VlessTest(unittest.TestCase):
    REALITY = ("vless://uuid-1@h.com:443?security=reality&pbk=K&sid=ab&fp=chrome"
               "&flow=xtls-rprx-vision&type=tcp#x")

    def test_crc_stable_default(self):
        self.assertEqual(content_crc32(vless.parse(self.REALITY)), "a5ec25c9")

    def test_flow_passthrough(self):
        node = vless.parse("vless://u@h.com:443?security=tls&flow=xtls-rprx-vision-udp443#x")
        self.assertEqual(node["flow"], "xtls-rprx-vision-udp443")

    def test_flow_none_string(self):
        # раньше flow=None давало xtls-rprx-vision
        self.assertNotIn("flow", vless.parse("vless://u@h.com:443?security=tls&flow=None#x"))

    def test_flow_dropped_for_xhttp(self):
        node = vless.parse("vless://u@h.com:443?security=tls&type=xhttp&flow=xtls-rprx-vision#x")
        self.assertNotIn("flow", node)
        self.assertEqual(node["transport"]["type"], "xhttp")

    def test_packet_encoding(self):
        self.assertEqual(vless.parse(self.REALITY)["packet_encoding"], "xudp")
        node = vless.parse("vless://u@h.com:443?packetEncoding=packetaddr#x")
        self.assertEqual(node["packet_encoding"], "packetaddr")

    def test_shadowrocket_b64_netloc(self):
        node = vless.parse("vless://" + _b64("auto:uuid-2@h.com:443") + "?security=tls#x")
        self.assertEqual((node["uuid"], node["server"], node["server_port"]), ("uuid-2", "h.com", 443))


class TrojanTest(unittest.TestCase):
    def test_percent_encoded_password(self):
        self.assertEqual(trojan.parse("trojan://p%40ss@h.com:443#x")["password"], "p@ss")

    def test_brackets_in_password(self):
        node = trojan.parse("trojan://a[b]c@h.com:443#x")
        self.assertEqual((node["password"], node["server"]), ("a[b]c", "h.com"))

    def test_ipv6_server(self):
        node = trojan.parse("trojan://pw@[2001:db8::1]:443#x")
        self.assertEqual((node["server"], node["server_port"]), ("2001:db8::1", 443))

    def test_ws_early_data(self):
        node = trojan.parse("trojan://pw@h.com:443?type=ws&path=%2Fws%3Fed%3D2048&host=h.com#x")
        self.assertEqual(node["transport"], {
            "type": "ws", "path": "/ws", "headers": {"Host": "h.com"},
            "early_data_header_name": "Sec-WebSocket-Protocol", "max_early_data": 2048})

    def test_ws_without_host(self):
        # раньше без host транспорт не создавался вовсе — нода шла как голый trojan
        node = trojan.parse("trojan://pw@h.com:443?type=ws&path=%2Fws#x")
        self.assertEqual(node["transport"], {"type": "ws", "path": "/ws", "headers": {}})

    def test_crc_stable_ws(self):
        node = trojan.parse("trojan://pass@h.com:443?sni=h.com&type=ws&path=%2Fws&host=h.com#x")
        self.assertEqual(content_crc32(node), "e07e6eaf")


class Hysteria2Test(unittest.TestCase):
    def test_no_default_bandwidth(self):
        # без up/down sing-box использует BBR; раньше подставлялось 10/100 (Brutal)
        node = hysteria2.parse("hysteria2://pw@h.com:443?sni=h.com#x")
        self.assertNotIn("up_mbps", node)
        self.assertNotIn("down_mbps", node)

    def test_explicit_bandwidth(self):
        node = hysteria2.parse("hysteria2://pw@h.com:443?sni=h.com&upmbps=50&downmbps=200#x")
        self.assertEqual((node["up_mbps"], node["down_mbps"]), (50, 200))

    def test_colon_port_range(self):
        node = hysteria2.parse("hysteria2://pw@h.com:20000-30000?sni=h.com#x")
        self.assertEqual((node["server_port"], node["server_ports"]), (20000, ["20000:30000"]))

    def test_comma_port_range(self):
        node = hysteria2.parse("hysteria2://pw@h.com:443,20000-30000?sni=h.com#x")
        self.assertEqual((node["server_port"], node["server_ports"]), (443, ["20000:30000"]))

    def test_mport(self):
        node = hysteria2.parse("hysteria2://pw@h.com:443?sni=h.com&mport=20000-30000#x")
        self.assertEqual(node["server_ports"], ["20000:30000"])


class SocksTest(unittest.TestCase):
    def test_b64_userinfo(self):
        node = socks.parse("socks://" + _b64("user:pass") + "@h.com:1080#x")
        self.assertEqual((node["username"], node["password"], node["server"], node["server_port"]),
                         ("user", "pass", "h.com", 1080))

    def test_plain_userinfo(self):
        node = socks.parse("socks://user:pass@h.com:1080#x")
        self.assertEqual((node["username"], node["password"]), ("user", "pass"))
        self.assertNotIn("udp_over_tcp", node)


class ClashTest(unittest.TestCase):
    VLESS = {"type": "vless", "name": "n", "server": "h.com", "port": 443, "uuid": "u",
             "network": "tcp", "tls": True, "servername": "h.com"}

    def test_vless_encryption_single_value(self):
        link = clash2base64.clash2v2ray(dict(self.VLESS, encryption="mlkem768x25519plus.native.0rtt.K"))
        self.assertEqual(link.count("encryption="), 1)
        self.assertEqual(vless.parse(link)["encryption"], "mlkem768x25519plus.native.0rtt.K")

    def test_vless_default_encryption(self):
        link = clash2base64.clash2v2ray(self.VLESS)
        self.assertIn("encryption=none&", link)
        self.assertNotIn("encryption", vless.parse(link))

    def test_vless_packet_encoding(self):
        link = clash2base64.clash2v2ray(dict(self.VLESS, **{"packet-encoding": "packetaddr"}))
        self.assertEqual(vless.parse(link)["packet_encoding"], "packetaddr")
        self.assertEqual(vless.parse(clash2base64.clash2v2ray(self.VLESS))["packet_encoding"], "xudp")

    def test_hy2_ports(self):
        link = clash2base64.clash2v2ray({"type": "hysteria2", "name": "n", "server": "h.com",
                                         "port": 443, "ports": "20000-30000", "password": "pw"})
        node = hysteria2.parse(link)
        self.assertEqual((node["server_port"], node["server_ports"]), (443, ["20000:30000"]))


if __name__ == "__main__":
    unittest.main()
