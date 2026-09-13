"""Балансировка: fair-share даёт равномерность ПО ПРОВАЙДЕРУ независимо от числа нод;
emergency тоже балансируется и уводит от провайдера упавшей ноды."""

import collections
import unittest

from nodes_tester.switcher import _weighted_choice
from tests.helpers import make_runner, temp_dir

N = 8000


def _pool(counts, score=80):
    pool = []
    for prov, n in counts.items():
        for i in range(n):
            pool.append({"node": f"{prov}{i}", "score": score, "provider": prov,
                         "country": "nl", "protocol": "vless"})
    return pool


class FairShareTest(unittest.TestCase):
    def _dist(self, pool, strengths):
        cnt = collections.Counter()
        for _ in range(N):
            cnt[_weighted_choice(pool, {}, dims={}, strengths=strengths)["provider"]] += 1
        return {k: v / N for k, v in cnt.items()}

    def test_parity_independent_of_node_count(self):
        pool = _pool({"A": 16, "B": 4})
        d = self._dist(pool, {"provider": 1.0})
        self.assertAlmostEqual(d["A"], 0.5, delta=0.05)      # равномерно по провайдеру
        self.assertAlmostEqual(d["B"], 0.5, delta=0.05)

    def test_strength_zero_is_proportional(self):
        pool = _pool({"A": 16, "B": 4})
        d = self._dist(pool, {"provider": 0.0})
        self.assertAlmostEqual(d["A"], 0.8, delta=0.05)      # пропорционально числу нод
        self.assertAlmostEqual(d["B"], 0.2, delta=0.05)

    def test_three_providers_parity(self):
        pool = _pool({"A": 20, "B": 10, "C": 2})
        d = self._dist(pool, {"provider": 1.0})
        for p in ("A", "B", "C"):
            self.assertAlmostEqual(d[p], 1 / 3, delta=0.06)


class EmergencyBalanceTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())

    def tearDown(self):
        self.r.storage.close()

    def test_emergency_avoids_blocked_provider(self):
        others = [{"node": "VSPACE-a [1]", "score": 90, "provider": "VSPACE",
                   "country": "nl", "protocol": "vless"},
                  {"node": "LUNA-b [2]", "score": 60, "provider": "LUNA",
                   "country": "de", "protocol": "hy2"}]
        # упала VSPACE-нода → emergency НЕ должен прыгать на другую VSPACE
        cnt = collections.Counter()
        for _ in range(300):
            pick = self.r.switcher._pick_emergency(
                others, {"activations": {}}, "VSPACE-vless|reality-nl-out [zzzz]")
            cnt[pick["provider"]] += 1
        self.assertEqual(cnt["VSPACE"], 0)                   # ушли от заблокированного провайдера
        self.assertEqual(cnt["LUNA"], 300)

    def test_emergency_falls_back_if_only_same_provider(self):
        # если живы только ноды того же провайдера — берём их (не застреваем)
        others = [{"node": "VSPACE-a [1]", "score": 90, "provider": "VSPACE",
                   "country": "nl", "protocol": "vless"}]
        pick = self.r.switcher._pick_emergency(
            others, {"activations": {}}, "VSPACE-vless|reality-nl-out [zzzz]")
        self.assertEqual(pick["provider"], "VSPACE")


if __name__ == "__main__":
    unittest.main()
