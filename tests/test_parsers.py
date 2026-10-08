"""Парсеры share-ссылок и записей Clash (nodes_fetch.parsers).

Отпечатки CRC закреплены: смена вывода парсера для неизменной ссылки меняет теги нод
и обнуляет их историю в тестере.
"""

import base64
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from naming.crc import content_crc32  # noqa: E402
from nodes_fetch.parsers import LinkError, clash, parse_link  # noqa: E402
from nodes_fetch.parsers._common import split_link  # noqa: E402

U = "b831381d-6324-4d53-ad4f-8cda48b30811"


def b64(text):
    return base64.b64encode(text.encode()).decode()


def one(link):
    nodes = parse_link(link)
    assert len(nodes) == 1, nodes
    return nodes[0]


class SplitLinkTest(unittest.TestCase):
    def test_parts(self):
        link = split_link("trojan://p%40ss@[2001:db8::1]:443/x?sni=s&sni=t&fp=&k=a+b#%D0%B8%D0%BC%D1%8F")
        self.assertEqual((link.user, link.host, link.port, link.path), ("p@ss", "2001:db8::1", "443", "/x"))
        self.assertEqual(link.params, {"sni": "s", "k": "a+b"})   # первое значение, пустые прочь, '+' литерал
        self.assertEqual(link.name, "имя")

    def test_slash_and_brackets_in_userinfo(self):
        link = split_link("ss://YWJj/ZA==@h.example:8388#n")
        self.assertEqual((link.userinfo, link.host), ("YWJj/ZA==", "h.example"))
        self.assertEqual(split_link("trojan://a[b]c@h.example:443").user, "a[b]c")

    def test_unknown_scheme_skipped(self):
        self.assertEqual(parse_link("ssr://abc"), [])
        self.assertEqual(parse_link("not a link"), [])

    def test_bad_port(self):
        with self.assertRaises(LinkError):
            parse_link("vless://u@h.example:http#n")


class VlessTest(unittest.TestCase):
    REALITY = ("vless://uuid-1@h.com:443?security=reality&pbk=K&sid=ab&fp=chrome"
               "&flow=xtls-rprx-vision&type=tcp#x")

    def test_reality(self):
        node = one(self.REALITY)
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False, "server_name": "",
                                       "utls": {"enabled": True, "fingerprint": "chrome"},
                                       "reality": {"enabled": True, "public_key": "K", "short_id": "ab"}})
        self.assertEqual((node["flow"], node["packet_encoding"]), ("xtls-rprx-vision", "xudp"))
        self.assertEqual(content_crc32(node), "a5ec25c9")

    def test_reality_without_fp(self):
        node = one(f"vless://{U}@h.com:443?security=reality&pbk=K&sid=None#x")
        self.assertEqual(node["tls"]["utls"], {"enabled": True})
        self.assertNotIn("short_id", node["tls"]["reality"])

    def test_flow(self):
        self.assertEqual(one("vless://u@h.com:443?security=tls&flow=xtls-rprx-vision-udp443#x")["flow"],
                         "xtls-rprx-vision")
        with self.assertRaises(LinkError):
            one("vless://u@h.com:443?security=tls&flow=xtls-rprx-direct#x")
        self.assertNotIn("flow", one("vless://u@h.com:443?security=tls&flow=None#x"))
        node = one("vless://u@h.com:443?security=tls&type=xhttp&flow=xtls-rprx-vision#x")
        self.assertNotIn("flow", node)

    def test_packet_encoding(self):
        self.assertEqual(one("vless://u@h.com:443?packetEncoding=packetaddr#x")["packet_encoding"], "packetaddr")
        self.assertNotIn("packet_encoding", one("vless://u@h.com:443?packetEncoding=none#x"))

    def test_ws_host_and_sni(self):
        node = one("vless://u@h.com:443?security=tls&type=ws&path=%2Fws%3Fed%3D2048&host=w.com#x")
        self.assertEqual(node["transport"], {"type": "ws", "path": "/ws", "headers": {"Host": "w.com"},
                                             "early_data_header_name": "Sec-WebSocket-Protocol",
                                             "max_early_data": 2048})
        self.assertEqual(node["tls"]["server_name"], "w.com")      # SNI из Host, если sni нет
        node = one("vless://u@h.com:443?security=tls&sni=s.com&type=ws#x")
        self.assertEqual(node["transport"]["headers"], {"Host": "s.com"})
        self.assertNotIn("headers", one("vless://u@h.com:443?type=ws#x")["transport"])

    def test_transports(self):
        self.assertEqual(one("vless://u@h.com:443?type=grpc&serviceName=svc#x")["transport"],
                         {"type": "grpc", "service_name": "svc"})
        self.assertEqual(one("vless://u@h.com:443?type=http&host=a.com&path=%2Fp#x")["transport"],
                         {"type": "http", "host": "a.com", "path": "/p"})
        self.assertEqual(one("vless://u@h.com:443?type=httpupgrade&host=a.com#x")["transport"],
                         {"type": "httpupgrade", "path": "/", "host": "a.com"})
        self.assertNotIn("transport", one("vless://u@h.com:443?type=tcp#x"))

    def test_encryption_and_mux(self):
        node = one("vless://u@h.com:443?encryption=mlkem768x25519plus.native.0rtt.K"
                   "&protocol=smux&max-streams=8&padding=True#x")
        self.assertEqual(node["encryption"], "mlkem768x25519plus.native.0rtt.K")
        self.assertEqual(node["multiplex"], {"enabled": True, "protocol": "smux", "max_streams": 8,
                                             "padding": True})
        self.assertNotIn("encryption", one("vless://u@h.com:443?encryption=none#x"))

    def test_shadowrocket(self):
        node = one("vless://" + b64("auto:uuid-2@h.com:443") + "?security=tls&remarks=SR")
        self.assertEqual((node["uuid"], node["server"], node["server_port"], node["tag"]),
                         ("uuid-2", "h.com", 443, "SR"))

    def test_name_fallback(self):
        self.assertEqual(one("vless://u@h.com:443")["tag"], "vless h.com:443")


class VmessTest(unittest.TestCase):
    ITEM = {"v": "2", "ps": "ps-name", "add": "a.com", "port": "443", "id": "u-1",
            "net": "ws", "host": "h.com", "path": "/p", "tls": "tls", "sni": "h.com"}

    def link(self, **fields):
        return "vmess://" + b64(json.dumps(dict(self.ITEM, **fields)))

    def test_ws_tls(self):
        node = one(self.link())
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False, "server_name": "h.com"})
        self.assertEqual(node["transport"], {"type": "ws", "path": "/p", "headers": {"Host": "h.com"}})
        self.assertEqual((node["security"], node["alter_id"], node["packet_encoding"]), ("auto", 0, "xudp"))
        self.assertEqual(content_crc32(node), "3aa87dd7")

    def test_names(self):
        self.assertEqual(one(self.link())["tag"], "ps-name")
        self.assertEqual(one(self.link() + "#%F0%9F%87%A9%F0%9F%87%AA%20DE")["tag"], "🇩🇪 DE")

    def test_tcp_http_header(self):
        node = one(self.link(net="tcp", type="http", host="vk.com", path="/", tls="none"))
        self.assertEqual(node["transport"], {"type": "http", "host": "vk.com", "path": "/"})
        self.assertNotIn("tls", node)
        self.assertNotIn("transport", one(self.link(net="tcp", type="none", tls="")))

    def test_tls_options(self):
        node = one(self.link(net="grpc", path="svc", fp="chrome", alpn="h2", allowInsecure="1"))
        self.assertEqual(node["transport"], {"type": "grpc", "service_name": "svc"})
        self.assertEqual(node["tls"]["utls"], {"enabled": True, "fingerprint": "chrome"})
        self.assertEqual((node["tls"]["alpn"], node["tls"]["insecure"]), (["h2"], True))

    def test_cipher(self):
        self.assertEqual(one(self.link(scy="aes-128-gcm"))["security"], "aes-128-gcm")
        self.assertEqual(one(self.link(scy="gun"))["security"], "auto")

    def test_not_json(self):
        with self.assertRaises(ValueError):
            parse_link("vmess://" + b64("garbage"))


class TrojanTest(unittest.TestCase):
    def test_password_and_address(self):
        self.assertEqual(one("trojan://p%40ss@h.com:443#x")["password"], "p@ss")
        node = one("trojan://a[b]c@[2001:db8::1]:443#x")
        self.assertEqual((node["password"], node["server"], node["server_port"]),
                         ("a[b]c", "2001:db8::1", 443))

    def test_tls(self):
        node = one("trojan://pw@h.com:443?sni=s.com&allowInsecure=1&alpn=h2,http/1.1&fp=firefox#x")
        self.assertEqual(node["tls"], {"enabled": True, "insecure": True, "server_name": "s.com",
                                       "alpn": ["h2", "http/1.1"],
                                       "utls": {"enabled": True, "fingerprint": "firefox"}})
        self.assertEqual(one("trojan://pw@h.com:443#x")["tls"], {"enabled": True, "insecure": False})

    def test_ws(self):
        node = one("trojan://pw@h.com:443?type=ws&path=%2Fws%3Fed%3D2048&host=h.com#x")
        self.assertEqual(node["transport"], {
            "type": "ws", "path": "/ws", "headers": {"Host": "h.com"},
            "early_data_header_name": "Sec-WebSocket-Protocol", "max_early_data": 2048})
        self.assertEqual(one("trojan://pw@h.com:443?type=ws&path=%2Fws#x")["transport"],
                         {"type": "ws", "path": "/ws", "headers": {}})
        node = one("trojan://pass@h.com:443?sni=h.com&type=ws&path=%2Fws&host=h.com#x")
        self.assertEqual(content_crc32(node), "e07e6eaf")

    def test_h2(self):
        self.assertEqual(one("trojan://pw@h.com:443?type=h2#x")["transport"],
                         {"type": "http", "host": "h.com", "path": "/"})


class ShadowsocksTest(unittest.TestCase):
    def test_sip002(self):
        node = one("ss://" + b64("aes-256-gcm:pa:ss") + "@h.com:8388#x")
        self.assertEqual((node["method"], node["password"], node["server"], node["server_port"]),
                         ("aes-256-gcm", "pa:ss", "h.com", 8388))

    def test_plain_userinfo_2022(self):
        node = one("ss://2022-blake3-aes-128-gcm:YWJjZGVmZ2hpamtsbW5vcA%3D%3D@h.com:8388#x")
        self.assertEqual(node["password"], "YWJjZGVmZ2hpamtsbW5vcA==")

    def test_legacy(self):
        node = one("ss://" + b64("chacha20-poly1305:pw@h.com:8388") + "#x")
        self.assertEqual((node["method"], node["server"]), ("chacha20-ietf-poly1305", "h.com"))

    def test_plugin_and_uot(self):
        node = one("ss://" + b64("aes-128-gcm:pw") + "@h.com:8388/?plugin=simple-obfs%3Bobfs%3Dhttp"
                   "%3Bobfs-host%3Dw.com&uot=1#x")
        self.assertEqual((node["plugin"], node["plugin_opts"]), ("obfs-local", "obfs=http;obfs-host=w.com"))
        self.assertEqual(node["udp_over_tcp"], {"enabled": True, "version": 2})


class HysteriaTest(unittest.TestCase):
    def test_hy2(self):
        node = one("hy2://pw@h.com:443?sni=s.com&obfs=salamander&obfs-password=op#x")
        self.assertEqual(node["type"], "hysteria2")
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False, "server_name": "s.com",
                                       "alpn": ["h3"]})
        self.assertEqual(node["obfs"], {"type": "salamander", "password": "op"})
        self.assertNotIn("up_mbps", node)

    def test_hy2_auth_and_ports(self):
        node = one("hysteria2://user:pw@h.com:443,20000-30000/?upmbps=50&downmbps=200#x")
        self.assertEqual(node["password"], "user:pw")
        self.assertEqual((node["server_port"], node["server_ports"]), (443, ["20000:30000"]))
        self.assertEqual((node["up_mbps"], node["down_mbps"]), (50, 200))
        self.assertEqual(one("hysteria2://pw@h.com:20000-30000#x")["server_port"], 20000)
        self.assertEqual(one("hysteria2://pw@h.com:443?mport=20000-30000#x")["server_ports"], ["20000:30000"])

    def test_hy2_without_sni_verifies(self):
        self.assertFalse(one("hysteria2://pw@1.2.3.4:443#x")["tls"]["insecure"])

    def test_hysteria1(self):
        node = one("hysteria://h.com:443?auth=au&peer=s.com&upmbps=20&downmbps=100&obfs=xplus&obfsParam=ob#x")
        self.assertEqual((node["auth_str"], node["obfs"], node["up_mbps"], node["down_mbps"]),
                         ("au", "ob", 20, 100))
        self.assertEqual(node["tls"]["server_name"], "s.com")


class OtherProtocolsTest(unittest.TestCase):
    def test_tuic(self):
        node = one(f"tuic://{U}:pw@h.com:443?congestion_control=bbr&udp_relay_mode=quic&sni=s.com#x")
        self.assertEqual((node["uuid"], node["password"], node["congestion_control"], node["udp_relay_mode"]),
                         (U, "pw", "bbr", "quic"))
        self.assertEqual(node["tls"], {"enabled": True, "alpn": ["h3"], "insecure": False, "server_name": "s.com"})
        self.assertTrue(one(f"tuic://{U}:pw@h.com:443?disable_sni=1#x")["tls"]["disable_sni"])

    def test_anytls(self):
        node = one("anytls://pw@h.com:443/?sni=s.com&insecure=1&idleSessionTimeout=30#x")
        self.assertEqual((node["password"], node["idle_session_timeout"]), ("pw", "30s"))
        self.assertEqual(node["tls"], {"enabled": True, "insecure": True, "server_name": "s.com"})

    def test_socks(self):
        for link in ("socks://" + b64("user:pw") + "@h.com:1080#x", "socks5://user:pw@h.com:1080#x"):
            node = one(link)
            self.assertEqual((node["username"], node["password"], node["server_port"]), ("user", "pw", 1080))
        node = one("socks://" + b64("user:pw@h.com:1080") + "#x")
        self.assertEqual((node["server"], node["username"], node["tag"]), ("h.com", "user", "x"))

    def test_http(self):
        self.assertNotIn("tls", one("http://user:pw@h.com:8080#x"))
        node = one("https://h.com:443?sni=s.com#x")
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False, "server_name": "s.com"})

    def test_wireguard(self):
        node = one("wireguard://PRIV%2Bk%3D@h.com:51820?publickey=PUB+k=&address=10.0.0.2,fd00::2"
                   "&reserved=1,2,3&mtu=1280#x")
        self.assertEqual((node["private_key"], node["address"], node["mtu"]),
                         ("PRIV+k=", ["10.0.0.2/32", "fd00::2/128"], 1280))
        self.assertEqual(node["peers"][0]["public_key"], "PUB+k=")
        self.assertEqual(node["peers"][0]["reserved"], [1, 2, 3])


class ClashTest(unittest.TestCase):
    def test_vless_reality(self):
        node = clash.convert({"type": "vless", "name": "n", "server": "h.com", "port": 443, "uuid": U,
                              "network": "grpc", "tls": True, "servername": "s.com", "flow": "",
                              "client-fingerprint": "chrome", "grpc-opts": {"grpc-service-name": "svc"},
                              "reality-opts": {"public-key": "K", "short-id": "ab"},
                              "encryption": "mlkem768x25519plus.native.0rtt.K"})
        self.assertEqual(node["tls"]["reality"], {"enabled": True, "public_key": "K", "short_id": "ab"})
        self.assertEqual(node["transport"], {"type": "grpc", "service_name": "svc"})
        self.assertEqual((node["packet_encoding"], node["encryption"]), ("xudp", "mlkem768x25519plus.native.0rtt.K"))

    def test_vmess_ws_sni_from_host(self):
        node = clash.convert({"type": "vmess", "name": "n", "server": "h.com", "port": 443, "uuid": U,
                              "alterId": 0, "cipher": "auto", "tls": True, "network": "ws",
                              "ws-opts": {"path": "/es", "headers": {"Host": "w.com"}}})
        self.assertEqual(node["tls"], {"enabled": True, "insecure": False, "server_name": "w.com"})
        self.assertEqual(node["transport"], {"type": "ws", "path": "/es", "headers": {"Host": "w.com"}})

    def test_ss_plugins(self):
        node = clash.convert({"type": "ss", "name": "n", "server": "h.com", "port": 8388, "cipher": "aes-128-gcm",
                              "password": "pw", "plugin": "obfs", "plugin-opts": {"mode": "tls", "host": "w.com"}})
        self.assertEqual(node["plugin_opts"], "obfs=tls;obfs-host=w.com")
        ss, stls = clash.convert({"type": "ss", "name": "n", "server": "h.com", "port": 443,
                                  "cipher": "aes-128-gcm", "password": "pw", "plugin": "shadow-tls",
                                  "plugin-opts": {"host": "w.com", "password": "sp", "version": 3}})
        self.assertEqual((ss["detour"], stls["tag"], stls["version"]), ("n_shadowtls", "n_shadowtls", 3))
        self.assertNotIn("server", ss)

    def test_hy2_and_unknown(self):
        node = clash.convert({"type": "hysteria2", "name": "n", "server": "h.com", "port": 443,
                              "ports": "20000-30000", "password": "pw", "sni": "s.com", "up": "50 Mbps"})
        self.assertEqual((node["server_ports"], node["up_mbps"], node["tls"]["server_name"]),
                         (["20000:30000"], 50, "s.com"))
        self.assertIsNone(clash.convert({"type": "ssr", "name": "n", "server": "h", "port": 1}))


if __name__ == "__main__":
    unittest.main()
