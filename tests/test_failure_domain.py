"""Домен отказа ноды — IPv4 /24 адреса входа: штраф и резерв считают подсеть одним целым."""
import unittest

from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir
from tests.test_reserve import A, B, C, D, E, _Base


class FailureDomainTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())
        self.addCleanup(self.r.storage.close)

    def _domain(self, node, server, ips=None):
        self.r._endpoints = {parse_node(node).node_id: (server, 443)}
        self.r._entry_ips = ips or {}
        return self.r._failure_domain(node)

    def test_ipv4_server(self):
        self.assertEqual(self._domain(A, "188.72.110.52"), "188.72.110.0/24")

    def test_domain_with_cached_ips(self):
        self.assertEqual(self._domain(A, "h.example", {"h.example": ["2001:db8::1", "5.6.7.8"]}),
                         "5.6.7.0/24")

    def test_domain_without_cache_and_ipv6_stay_as_is(self):
        self.assertEqual(self._domain(A, "h.example"), "h.example")
        self.assertEqual(self._domain(A, "2001:db8::1"), "2001:db8::1")

    def test_unknown_server_is_empty(self):
        self.r._endpoints = {}
        self.assertEqual(self.r._failure_domain(A), "")


class SubnetTest(_Base):
    def setUp(self):
        super().setUp()
        servers = {A: "1.2.3.4", B: "1.2.3.99", C: "1.2.4.4", D: "h.example", E: "5.6.7.8"}
        self.r._endpoints = {parse_node(n).node_id: (s, 443) for n, s in servers.items()}
        self.r._entry_ips = {"h.example": ["1.2.4.7"]}

    def test_same_subnet_same_host(self):
        sw = self.r.switcher
        self.assertEqual(sw.host(A), sw.host(B))
        self.assertNotEqual(sw.host(A), sw.host(C))
        self.assertEqual(sw.host(C), sw.host(D))          # домен с IP из той же /24

    def test_penalty_covers_neighbour_ip(self):
        sw = self.r.switcher
        sw.penalize(E)
        self.assertIn(sw.host(E), sw.avoided_hosts("us"))
        sw.penalize(C)
        self.assertIn(sw.host(D), sw.avoided_hosts("us"))
        self.assertNotIn(sw.host(B), sw.avoided_hosts("us"))

    def test_reserve_one_per_subnet(self):
        r = self.r
        r.reserve.maintain("eu")
        self.assertEqual(r.board.reserve("eu"), [C, E])   # B — подсеть активной A, D — подсеть C


if __name__ == "__main__":
    unittest.main()
