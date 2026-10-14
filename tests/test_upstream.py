"""Проверка upstream: через ноду проходит вышестоящий SOCKS-прокси (outbound с detour на группу)."""

import json
import os
import socket
import struct
import threading
import unittest

from nodes_tester.config import ConfigError, parse_config
from nodes_tester.proxy import make_session
from nodes_tester.tests import TestContext
from nodes_tester.tests.upstream import UpstreamTest, exit_ip
from tests.helpers import temp_dir

PROXY = ("192.0.2.10", 9062)


def _read(conn, size):
    data = b""
    while len(data) < size:
        data += conn.recv(size - len(data))
    return data


def _connect_target(conn):
    head = _read(conn, 4)
    if head[3] == 1:
        host = socket.inet_ntoa(_read(conn, 4))
    else:
        host = _read(conn, _read(conn, 1)[0]).decode()
    port = struct.unpack(">H", _read(conn, 2))[0]
    conn.sendall(b"\x05\x00\x00\x01" + bytes(4) + b"\x00\x00")
    return host, port


class FakeChain:
    """SOCKS тестера (без логина) и за ним вышестоящий прокси (с логином) в одном потоке.
    inner_drop — прокси закрывает соединение сразу (как прокси, не принимающий IP ноды)."""

    def __init__(self, inner_drop=False):
        self.inner_drop = inner_drop
        self.seen = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                _read(conn, 3)
                conn.sendall(b"\x05\x00")
                self.seen.append(_connect_target(conn))           # сервер прокси
                if self.inner_drop:
                    continue
                _read(conn, 3)
                conn.sendall(b"\x05\x02")
                ulen = _read(conn, 2)[1]
                user = _read(conn, ulen).decode()
                password = _read(conn, _read(conn, 1)[0]).decode()
                self.seen.append((user, password))
                conn.sendall(b"\x01\x00")
                self.seen.append(_connect_target(conn))           # цель запроса
                request = b""
                while not request.endswith(b"\r\n\r\n"):
                    request += conn.recv(1024)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nfl=1\nip=45.92.210.251\n")

    def close(self):
        self.server.close()


def _singbox_config():
    path = os.path.join(temp_dir(), "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"outbounds": [{"type": "socks", "tag": "nl-static-out", "server": PROXY[0],
                                  "server_port": PROXY[1], "username": "u", "password": "p",
                                  "detour": "ai-auto-out"}]}, fh)
    return path


class UpstreamTestCase(unittest.TestCase):
    def _run(self, chain, outbound="nl-static-out"):
        self.addCleanup(chain.close)
        test = UpstreamTest({"outbound": outbound, "singbox_config": _singbox_config(),
                             "url": "http://example.test/cdn-cgi/trace", "attempts": 2, "timeout": 5})
        ctx = TestContext(session=make_session(f"socks5h://127.0.0.1:{chain.port}"), node="n",
                          default_timeout=5)
        return test.run(ctx).to_dict()

    def test_pass_through_node(self):
        chain = FakeChain()
        res = self._run(chain)
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_ip"], "45.92.210.251")
        self.assertEqual(chain.seen[:3], [PROXY, ("u", "p"), ("example.test", 80)])

    def test_proxy_drops_connection(self):
        res = self._run(FakeChain(inner_drop=True))
        self.assertFalse(res["ok"])
        self.assertNotIn("inconclusive", res)
        self.assertTrue(res["error"])        # EOF или сброс — смотря как ОС закрыла сокет

    def test_unknown_outbound_is_inconclusive(self):
        res = self._run(FakeChain(), outbound="missing-out")
        self.assertFalse(res["ok"])
        self.assertTrue(res["inconclusive"])

    def test_exit_ip(self):
        self.assertEqual(exit_ip(b"fl=1\nip=2001:db8::1\nts=1"), "2001:db8::1")
        self.assertEqual(exit_ip(b"203.0.113.7\n"), "203.0.113.7")
        self.assertEqual(exit_ip(b"<html>"), "")


class UpstreamConfigTest(unittest.TestCase):
    def test_group_requires_outbound(self):
        with self.assertRaises(ConfigError) as err:
            parse_config({"groups": [{"tag": "ai", "require": {"upstream": {}}}]})
        self.assertIn("checks.upstream.outbound", str(err.exception))
        cfg = parse_config({"checks": {"upstream": {"outbound": "nl-static-out"}},
                            "groups": [{"tag": "ai", "require": {"upstream": {}}}]})
        self.assertEqual(cfg.group("ai").require["upstream"]["ttl"], 21600.0)


if __name__ == "__main__":
    unittest.main()
