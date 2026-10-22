"""Боевая надёжность в рейтинге (selection.stability): аварии на час работы активной,
сглаженные к частоте поставщика."""

import os
import time
import unittest

from nodes_tester import scoring
from nodes_tester.config import RatingConfig, StabilityConfig, StorageConfig
from nodes_tester.scoreboard import Scoreboard
from nodes_tester.storage import Storage
from tests.helpers import temp_dir


class FactorTest(unittest.TestCase):
    def test_formula(self):
        cfg = StabilityConfig()
        self.assertAlmostEqual(scoring.stability_factor(0, 0, 0.0, cfg), 1.0)
        self.assertAlmostEqual(scoring.stability_factor(0, 0, 0.35, cfg), 1 / 1.35)   # без истории — поставщик
        # 3 аварии за час у ноды поставщика с частотой 0.35: (3 + 2·0.35) / 3 ≈ 1.23 в час
        self.assertAlmostEqual(scoring.stability_factor(3, 1, 0.35, cfg), 1 / (1 + 3.7 / 3))
        self.assertEqual(scoring.stability_factor(100, 1, 1.0, cfg), cfg.min)


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(StorageConfig(path=os.path.join(temp_dir(), "s.db")))
        self.now = 1_000_000.0

    def tearDown(self):
        self.storage.close()

    def _act(self, ago_h, region, crc, reason):
        self.storage._db.execute("INSERT INTO activations (ts,region,crc,tag,reason,score,prev) "
                                 "VALUES (?,?,?,?,?,0,'')", (int(self.now - ago_h * 3600), region, crc, crc, reason))

    def test_hours_and_failures(self):
        self._act(10, "ai", "A", "rotation")           # A работает 4 ч и падает
        self._act(6, "ai", "B", "emergency")           # B 3 ч, уходит по ротации
        self._act(3, "ai", "C", "rotation")            # C активна до сих пор
        self._act(5, "eu", "A", "init")                # в eu A работает 5 ч
        stats = self.storage.stability_stats(7 * 86400, now=self.now)
        self.assertEqual(stats["B"], [3.0, 0])
        self.assertEqual(stats["C"], [3.0, 0])
        self.assertEqual([round(stats["A"][0], 3), stats["A"][1]], [9.0, 1])

    def test_manual_quarantine_is_not_a_failure(self):
        self._act(5, "ai", "A", "rotation")
        self._act(1, "ai", "B", "emergency")
        self.storage._db.execute("INSERT INTO node_events (ts,crc,event,reason,streak) VALUES (?,?,?,?,0)",
                                 (int(self.now - 3600 - 30), "A", "garbage", "manual"))
        self.assertEqual(self.storage.stability_stats(86400, now=self.now)["A"][1], 0)


class ScoreTest(unittest.TestCase):
    def test_unreliable_provider_loses_rating(self):
        storage = Storage(StorageConfig(path=os.path.join(temp_dir(), "s.db")))
        try:
            sb = Scoreboard(storage, RatingConfig())
            state = {"score": 90.0, "score_ewma": 90.0, "avail": 1.0, "flap": 0.0, "region": "eu",
                     "protocol": "vless", "country": "nl", "heavy_ok": "", "heavy_ts": 0}
            sb.rows = {n: {**state, "node": n, "id": n, "provider": p}
                       for n, p in (("A", "BAD"), ("B", "GOOD"), ("Z", "BAD"))}
            sb.rows["Z"]["score"] = 0.0                    # провал gate — остаётся 0
            sb.set_stability({"A": [10.0, 5], "B": [10.0, 0]}, StabilityConfig())
            self.assertEqual(sb.rows["B"]["score"], 90.0)
            self.assertLess(sb.rows["A"]["score"], 70.0)
            self.assertEqual(sb.rows["Z"]["score"], 0.0)
            self.assertLess(sb.stability("Z"), 1.0)        # новые замеры Z тоже получат множитель
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
