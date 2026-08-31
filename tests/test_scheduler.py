"""Host-aware обход: round-robin разносит одинаковые хосты; зазор ждёт только при
том же хосте с ДРУГИМ (порт/протокол), а не при том же endpoint."""

import time
import unittest

from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())
        self.r._host_ep_last = {}

    def tearDown(self):
        self.r.storage.close()

    def _nodes(self):
        ids = [parse_node(f"P-nl-vless(reality)-out [n{i}]") for i in range(5)]
        # n0,n1,n2 — один хост host1; n3=host2; n4=host3
        self.r._endpoints = {ids[0].node_id: ("host1", 443),
                             ids[1].node_id: ("host1", 8443),
                             ids[2].node_id: ("host1", 2083),
                             ids[3].node_id: ("host2", 443),
                             ids[4].node_id: ("host3", 443)}
        return [("eu", i) for i in ids]

    def test_order_round_robin_first_cycle_distinct_hosts(self):
        ordered = self.r._order_by_host(self._nodes())
        hosts = [self.r._host_of(i) for _, i in ordered]
        self.assertEqual(len(set(hosts[:3])), 3)          # первый круг — разные хосты

    def test_gap_waits_only_on_different_endpoint(self):
        slept = []
        real = time.sleep
        time.sleep = lambda s: slept.append(s)
        try:
            r = self.r
            r._respect_host_gap("H", (443, "vless"), 120)   # первый — без сна
            r._respect_host_gap("H", (443, "vless"), 120)   # тот же sig — без сна
            self.assertEqual(slept, [])
            r._respect_host_gap("H", (8443, "trojan"), 120)  # другой sig на том же хосте
            self.assertEqual(len(slept), 1)
            self.assertGreater(slept[0], 100)
        finally:
            time.sleep = real

    def test_gap_zero_never_sleeps(self):
        slept = []
        real = time.sleep
        time.sleep = lambda s: slept.append(s)
        try:
            self.r._respect_host_gap("H", (1, "a"), 0)
            self.r._respect_host_gap("H", (2, "b"), 0)
            self.assertEqual(slept, [])
        finally:
            time.sleep = real


if __name__ == "__main__":
    unittest.main()
