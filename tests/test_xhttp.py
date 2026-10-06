"""Парсинг XHTTP-транспорта (sing-box-lx, тег with_xhttp) в vless/trojan.

Проверяем: минимальный type=xhttp, packet-up с extra (число -> "N-N"),
reality + utls, stream-one с непустым path, битый extra (парсер не падает),
xmux вложенным объектом, а также заодно закрытые пробелы vless.py — alpn и
encryption. Плюс контракт с naming.node_protocol / groups.UNGROUPED_PROTOCOLS:
XHTTP-нода больше НЕ считается "голым" vless и не отбрасывается.
"""

import json
import os
import sys
import unittest
from urllib.parse import quote


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_config import groups  # noqa: E402
from nodes_fetch.parsers import _common as tool  # noqa: E402  (xhttp_range/xhttp_transport)
from nodes_fetch.parsers import vless, trojan  # noqa: E402
from naming import node_protocol  # noqa: E402


class XhttpRangeTest(unittest.TestCase):
    def test_single_int(self):
        self.assertEqual(tool.xhttp_range(30), "30-30")

    def test_float_dropped(self):
        self.assertEqual(tool.xhttp_range(30.0), "30-30")

    def test_string_number(self):
        self.assertEqual(tool.xhttp_range("1000000"), "1000000-1000000")

    def test_already_range(self):
        self.assertEqual(tool.xhttp_range("100-1000"), "100-1000")

    def test_list_pair(self):
        self.assertEqual(tool.xhttp_range([1800, 3000]), "1800-3000")


class XhttpVlessTest(unittest.TestCase):
    def test_minimal(self):
        link = ("vless://c59e@host.example:443?type=xhttp"
                "&host=oh6.example.net&path=%2F")
        node = vless.parse(link)
        self.assertEqual(node["transport"], {
            "type": "xhttp", "path": "/", "host": "oh6.example.net"})

    def test_minimal_auto(self):
        # Живой кейс провайдера: только mode=auto + path, без host/extra.
        link = ("vless://9e54@de1.provider.example:8080?security=tls"
                "&sni=de1.provider.example&alpn=h2&type=xhttp"
                "&path=%2Fabcdef012345&mode=auto#de1")
        node = vless.parse(link)
        self.assertEqual(node["transport"],
                         {"type": "xhttp", "path": "/abcdef012345", "mode": "auto"})
        self.assertNotIn("host", node["transport"])
        self.assertEqual(node["tls"]["alpn"], ["h2"])
        self.assertNotIn("flow", node)
        # Протокол больше не "голый" vless -> нода не отбрасывается фильтром
        proto = node_protocol(node)
        self.assertEqual(proto, "vless|xhttp|tls")
        self.assertNotIn(proto, groups.UNGROUPED_PROTOCOLS)

    def test_packet_up_extra(self):
        # extra: scMinPostsIntervalMs=30.0 -> "30-30", scMaxEachPostBytes "1000000" -> "1000000-1000000"
        extra = ('%7B%22scMaxEachPostBytes%22%3A%221000000%22%2C'
                 '%22scMinPostsIntervalMs%22%3A30.0%7D')
        link = ("vless://c59e@199.232.244.214:443?type=xhttp&mode=packet-up"
                "&security=tls&sni=manage.fastly.com&host=oh6.global.ssl.fastly.net"
                "&path=%2F&fp=chrome&extra=" + extra)
        node = vless.parse(link)
        t = node["transport"]
        self.assertEqual(t["mode"], "packet-up")
        self.assertEqual(t["sc_max_each_post_bytes"], "1000000-1000000")
        self.assertEqual(t["sc_min_posts_interval_ms"], "30-30")
        self.assertEqual(node["tls"]["utls"],
                         {"enabled": True, "fingerprint": "chrome"})

    def test_reality(self):
        link = ("vless://c59e@host.example:443?type=xhttp&security=reality"
                "&pbk=PUBKEY&sid=ab12&fp=chrome&path=%2F")
        node = vless.parse(link)
        self.assertTrue(node["tls"]["reality"]["enabled"])
        self.assertEqual(node["tls"]["reality"]["public_key"], "PUBKEY")
        self.assertEqual(node["tls"]["reality"]["short_id"], "ab12")
        self.assertEqual(node["tls"]["utls"]["fingerprint"], "chrome")
        self.assertEqual(node["transport"]["type"], "xhttp")

    def test_stream_one_keeps_trailing_slash(self):
        link = ("vless://c59e@host.example:443?type=xhttp&mode=stream-one"
                "&path=%2Fabc%2F")
        node = vless.parse(link)
        self.assertEqual(node["transport"]["mode"], "stream-one")
        self.assertEqual(node["transport"]["path"], "/abc/")

    def test_path_strips_query_suffix(self):
        link = ("vless://c59e@host.example:443?type=xhttp"
                "&path=%2Fabc%3Ffoo%3Dbar")
        node = vless.parse(link)
        self.assertEqual(node["transport"]["path"], "/abc")

    def test_broken_extra_does_not_crash(self):
        link = ("vless://c59e@host.example:443?type=xhttp&path=%2F"
                "&extra=not-a-json")
        node = vless.parse(link)  # не должно бросить
        self.assertEqual(node["transport"]["type"], "xhttp")
        self.assertNotIn("sc_max_each_post_bytes", node["transport"])

    def test_no_grpc_header_bool(self):
        link = ("vless://c59e@host.example:443?type=xhttp&path=%2F"
                "&noGRPCHeader=1")
        node = vless.parse(link)
        self.assertIs(node["transport"]["no_grpc_header"], True)

    def _xhttp_extra_link(self, obj):
        return ("vless://c59e@host.example:443?type=xhttp&path=%2F&extra="
                + quote(json.dumps(obj)))

    def test_xmux_nested(self):
        # xmux приходит вложенным объектом в extra (уже snake_case)
        link = self._xhttp_extra_link(
            {"xmux": {"max_concurrency": "1-1", "h_keep_alive_period": 45}})
        node = vless.parse(link)
        self.assertEqual(node["transport"]["xmux"],
                         {"max_concurrency": "1-1", "h_keep_alive_period": 45})

    def test_xmux_camelcase_mapped(self):
        # MEDVED/puxvpn шлют camelCase — без маппинга sing-box падает
        # (unknown field "cMaxReuseTimes"). Числа -> "N-N".
        link = self._xhttp_extra_link({"xmux": {
            "cMaxReuseTimes": 0, "maxConcurrency": "1-1", "maxConnections": 0,
            "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000"}})
        node = vless.parse(link)
        self.assertEqual(node["transport"]["xmux"], {
            "c_max_reuse_times": "0-0", "max_concurrency": "1-1",
            "max_connections": "0-0", "h_max_request_times": "600-900",
            "h_max_reusable_secs": "1800-3000"})

    def test_xmux_h_keep_alive_range_form(self):
        # h_keep_alive_period — int64, не диапазон: из "0-0" берём первое число
        link = self._xhttp_extra_link({"xmux": {"hKeepAlivePeriod": "0-0"}})
        node = vless.parse(link)
        self.assertEqual(node["transport"]["xmux"], {"h_keep_alive_period": 0})

    def test_xmux_h_keep_alive_negative(self):
        # <0 = выкл — сохраняем как есть (int)
        link = self._xhttp_extra_link({"xmux": {"h_keep_alive_period": -1}})
        node = vless.parse(link)
        self.assertEqual(node["transport"]["xmux"], {"h_keep_alive_period": -1})

    def test_encryption_passthrough(self):
        link = ("vless://c59e@host.example:443?type=xhttp&path=%2F"
                "&encryption=mlkem768x25519plus.native.0rtt.KEY")
        node = vless.parse(link)
        self.assertEqual(node["encryption"],
                         "mlkem768x25519plus.native.0rtt.KEY")

    def test_encryption_none_ignored(self):
        link = "vless://c59e@host.example:443?type=xhttp&path=%2F&encryption=none"
        node = vless.parse(link)
        self.assertNotIn("encryption", node)

    def test_string_map_camel_to_snake(self):
        link = ("vless://c59e@host.example:443?type=xhttp&path=%2F"
                "&xPaddingBytes=100-1000&uplinkHTTPMethod=PUT")
        node = vless.parse(link)
        self.assertEqual(node["transport"]["x_padding_bytes"], "100-1000")
        self.assertEqual(node["transport"]["uplink_http_method"], "PUT")


class XhttpTrojanTest(unittest.TestCase):
    def test_minimal(self):
        link = ("trojan://pass@host.example:443?type=xhttp"
                "&host=oh6.example.net&path=%2F&sni=host.example#t1")
        node = trojan.parse(link)
        self.assertEqual(node["transport"], {
            "type": "xhttp", "path": "/", "host": "oh6.example.net"})


if __name__ == "__main__":
    unittest.main()
