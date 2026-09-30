"""API-сервис sing-box: кодек protobuf, транспорт gRPC-Web, точный учёт трафика по потоку."""

import json
import struct
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nodes_common import box_api
from nodes_common.box_api import (BoxApi, BoxApiError, EVENT_CLOSED, EVENT_NEW, EVENT_UPDATE,
                                  connection_events, decode, encode)

LEAF = "DEMO-vless|reality-nl-out [0c0a0001]"
CHAIN = [LEAF, "eu-auto-out-failsafe", "eu-auto-out", "global-auto-out"]


def _conn(cid, chain=CHAIN, up=0, down=0, closed=0, source="192.168.1.20:5000",
          dest="1.2.3.4:443", domain="example.com"):
    fields = [box_api._field(1, cid), box_api._field(5, "tcp"), box_api._field(6, source),
              box_api._field(7, dest), box_api._field(8, domain)]
    if closed:
        fields.append(box_api._field(13, closed))
    if up:
        fields.append(box_api._field(16, up))
    if down:
        fields.append(box_api._field(17, down))
    fields += [box_api._field(21, x) for x in chain]
    return b"".join(fields)


def _event(etype, cid, conn=None, dup=0, ddown=0):
    body = encode({1: etype, 2: cid, 4: dup, 5: ddown})
    if conn is not None:
        body += box_api._field(3, conn)
    return body


def _events(*events, reset=False):
    return connection_events(decode(b"".join(box_api._field(1, e) for e in events)
                                    + (box_api._field(2, True) if reset else b"")))


class CodecTest(unittest.TestCase):
    def test_roundtrip_and_repeated(self):
        msg = decode(encode({1: "grp", 2: "node", 3: 5}) + box_api._field(21, "a")
                     + box_api._field(21, "b"))
        self.assertEqual(msg[1], [b"grp"])
        self.assertEqual(msg[3], [5])
        self.assertEqual(msg[21], [b"a", b"b"])

    def test_varint_large_and_negative(self):
        self.assertEqual(decode(encode({1: 5_000_000_000}))[1], [5_000_000_000])
        self.assertEqual(box_api._int(decode(box_api._field(1, -1)), 1), -1)

    def test_connection_event_fields(self):
        ev = _events(_event(EVENT_CLOSED, "c1", _conn("c1", up=530, down=2008896, closed=1)))
        e = ev["events"][0]
        self.assertEqual(e["type"], EVENT_CLOSED)
        self.assertEqual(e["connection"]["downlink_total"], 2008896)
        self.assertEqual(e["connection"]["chain"], CHAIN)
        self.assertFalse(ev["reset"])


def _grpc_frames(*messages, status="0", message=""):
    out = b"".join(b"\x00" + struct.pack(">I", len(m)) + m for m in messages)
    trailer = f"grpc-status:{status}\r\n" + (f"grpc-message:{message}\r\n" if message else "")
    return out + b"\x80" + struct.pack(">I", len(trailer)) + trailer.encode()


class _Server:
    """Фейковый API: path → (http_status, headers, body); запоминает запросы."""

    def __init__(self, routes):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, dict(self.headers), body))
                status, headers, payload = routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TransportTest(unittest.TestCase):
    def _api(self, routes, secret="s"):
        srv = _Server(routes)
        self.addCleanup(srv.close)
        return BoxApi(srv.url, secret, timeout=3), srv

    def test_unary_and_auth_header(self):
        path = box_api.SERVICE + "GetVersion"
        api, srv = self._api({path: (200, {}, _grpc_frames(encode({1: "1.14.2", 2: 4})))})
        self.assertEqual(api.version(), {"version": "1.14.2", "api_version": 4})
        _, headers, body = srv.requests[0]
        self.assertEqual(headers["authorization"], "Bearer s")
        self.assertEqual(headers["content-type"], "application/grpc-web+proto")
        self.assertEqual(body, b"\x00\x00\x00\x00\x00")

    def test_select_request_and_trailer_error(self):
        path = box_api.SERVICE + "SelectOutbound"
        api, srv = self._api({path: (200, {}, _grpc_frames(
            status="5", message="outbound%20not%20found"))})
        with self.assertRaisesRegex(BoxApiError, "grpc-status 5 outbound not found"):
            api.select("grp", "node")
        req = decode(srv.requests[0][2][5:])
        self.assertEqual(req, {1: [b"grp"], 2: [b"node"]})

    def test_trailers_only_error_in_headers(self):
        path = box_api.SERVICE + "GetVersion"
        api, _ = self._api({path: (200, {"grpc-status": "16",
                                         "grpc-message": "invalid authorization"}, b"")})
        with self.assertRaisesRegex(BoxApiError, "grpc-status 16"):
            api.version()

    def test_http_401_and_unreachable(self):
        api, _ = self._api({box_api.SERVICE + "GetVersion": (401, {}, b"")})
        with self.assertRaisesRegex(BoxApiError, "401"):
            api.version()
        with self.assertRaises(BoxApiError):
            BoxApi("http://127.0.0.1:1", timeout=1).version()

    def test_groups_snapshot(self):
        item = encode({1: LEAF, 2: "vless", 3: 1790762679, 4: 96})
        group = encode({1: "eu-auto-out", 2: "selector", 3: True, 4: LEAF}) + box_api._field(6, item)
        msg = box_api._field(1, group)
        api, _ = self._api({box_api.SERVICE + "SubscribeGroups": (200, {}, _grpc_frames(msg, msg))})
        g = api.groups()[0]
        self.assertEqual((g["tag"], g["type"], g["selected"]), ("eu-auto-out", "selector", LEAF))
        self.assertEqual(g["items"][0]["url_test_delay"], 96)
        self.assertEqual(box_api.group_delay(api, "eu-auto-out", 1790762600), 96)
        self.assertIsNone(box_api.group_delay(api, "eu-auto-out", 1790762700))

    def test_subscribe_connections_interval_in_ns(self):
        path = box_api.SERVICE + "SubscribeConnections"
        ev = box_api._field(1, _event(EVENT_NEW, "c1", _conn("c1")))
        api, srv = self._api({path: (200, {}, _grpc_frames(ev))})
        got = [connection_events(decode(m)) for m in api.subscribe_connections(2)]
        self.assertEqual(got[0]["events"][0]["id"], "c1")
        self.assertEqual(decode(srv.requests[0][2][5:]), {1: [2_000_000_000]})

    def test_from_singbox_config(self):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"services": [{"type": "api", "listen": "0.0.0.0", "listen_port": 9091,
                                     "secret": "x"}]}, fh)
        self.addCleanup(os.remove, path)
        self.assertEqual(box_api.from_singbox_config(path).url, "http://127.0.0.1:9091")


class _Cfg:
    poll_interval = 5
    flush_interval = 60


class CollectorTest(unittest.TestCase):
    def setUp(self):
        from nodes_tester.traffic import TrafficCollector
        self.col = TrafficCollector(_Cfg(), api=None, storage=None)

    def _node(self, is_tester=0):
        return self.col._by_node.get(("0c0a0001", is_tester), [0, 0, 0])

    def test_short_connection_counted_in_full(self):
        self.col.apply(_events(_event(EVENT_NEW, "c1", _conn("c1"))))
        self.col.apply(_events(_event(EVENT_UPDATE, "c1", dup=285)))
        self.col.apply(_events(_event(EVENT_UPDATE, "c1", dup=208, ddown=1508391)))
        self.col.apply(_events(_event(EVENT_CLOSED, "c1", _conn("c1", up=530, down=1508419,
                                                                  closed=1))))
        self.assertEqual(self._node(), [530, 1508419, 1])
        self.assertEqual(self.col.user_bytes(), {"0c0a0001": 530 + 1508419})
        self.assertEqual(self.col._conns, {})

    def test_closed_without_updates(self):
        self.col.apply(_events(_event(EVENT_NEW, "c1", _conn("c1"))))
        self.col.apply(_events(_event(EVENT_CLOSED, "c1", _conn("c1", up=10, down=2000,
                                                                  closed=1))))
        self.assertEqual(self._node(), [10, 2000, 1])

    def test_snapshot_is_baseline_not_counted(self):
        self.col.apply(_events(_event(EVENT_NEW, "old", _conn("old", up=100, down=900)),
                               _event(EVENT_NEW, "gone", _conn("gone", up=5, down=5, closed=1)),
                               reset=True))
        self.assertEqual(self._node(), [0, 0, 0])
        self.assertIn("old", self.col._conns)
        self.assertNotIn("gone", self.col._conns)          # закрытое в снимке не храним
        self.col.apply(_events(_event(EVENT_CLOSED, "old", _conn("old", up=150, down=1000,
                                                                   closed=1))))
        self.assertEqual(self._node(), [50, 100, 0])       # только прирост после снимка

    def test_reconnect_snapshot_adds_gap(self):
        self.col.apply(_events(_event(EVENT_NEW, "c1", _conn("c1"))))
        self.col.apply(_events(_event(EVENT_UPDATE, "c1", dup=10, ddown=100)))
        # разрыв; новый снимок: соединение живо и накачало больше
        self.col.apply(_events(_event(EVENT_NEW, "c1", _conn("c1", up=40, down=700)),
                               reset=True))
        self.assertEqual(self._node(), [40, 700, 1])
        self.col.apply(_events(_event(EVENT_CLOSED, "c1", _conn("c1", up=40, down=750,
                                                                  closed=1))))
        self.assertEqual(self._node(), [40, 750, 1])

    def test_tester_traffic_separated_and_endpoint(self):
        chain = [LEAF, "nodes-tester"]
        self.col.apply(_events(_event(EVENT_NEW, "t1", _conn("t1", chain=chain,
                                                             source="127.0.0.1:4000"))))
        self.col.apply(_events(_event(EVENT_CLOSED, "t1", _conn("t1", chain=chain, up=1,
                                                                  down=10_000_000, closed=1))))
        self.assertEqual(self._node(is_tester=1), [1, 10_000_000, 1])
        self.assertEqual(self.col.user_bytes(), {})
        self.assertIn(("0c0a0001", "127.0.0.1", "example.com", "tcp"), self.col._by_ep)

    def test_host_parsing(self):
        from nodes_tester.traffic import _host
        self.assertEqual(_host("1.2.3.4:443"), "1.2.3.4")
        self.assertEqual(_host("[::1]:53"), "::1")
        self.assertEqual(_host("example.com"), "example.com")


class MonitorDeltaTest(unittest.TestCase):
    def test_delta_between_ticks(self):
        from nodes_tester.monitor import ProductionMonitor
        counters = {"a": 100}
        mon = ProductionMonitor(cfg=None, api=None, switcher=None, prober=None,
                                traffic=lambda: dict(counters))
        self.assertEqual(mon._traffic_delta(), {})         # первый тик — точка отсчёта
        counters.update(a=250, b=40)
        self.assertEqual(mon._traffic_delta(), {"a": 150, "b": 40})
        self.assertEqual(mon._traffic_delta(), {})


if __name__ == "__main__":
    unittest.main()
