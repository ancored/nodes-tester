"""Heavy-veto как фильтр кандидатов: свежий veto исключает ноду, но при отсутствии
не-vetoed возвращаем vetoed (мало нод → выбираем из имеющихся)."""

import os
import unittest

from nodes_tester.config import ScoringConfig, StorageConfig
from nodes_tester.scoreboard import Scoreboard
from nodes_tester.storage import Storage
from tests.helpers import temp_dir


def _row(node, region="eu", score=70):
    return {"node": node, "region": region, "score": score, "provider": "P",
            "protocol": "vless", "country": "nl", "id": node, "heavy_ok": "", "heavy_ts": 0}


class ScoreboardVetoTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(StorageConfig(db_file=os.path.join(temp_dir(), "s.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.set_heavy_veto_ttl(3600)
        self.sb.rows = {"A": _row("A", score=80), "B": _row("B", score=60)}

    def tearDown(self):
        self.storage.close()

    def test_no_veto_returns_all_by_score(self):
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["A", "B"])

    def test_fresh_veto_excludes_node(self):
        self.sb.set_heavy("A", False)
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["B"])

    def test_all_vetoed_falls_back(self):
        self.sb.set_heavy("A", False)
        self.sb.set_heavy("B", False)
        self.assertEqual(sorted(c["node"] for c in self.sb.candidates("eu")), ["A", "B"])

    def test_heavy_columns_persist(self):
        self.sb.set_heavy("A", True)
        self.sb.write()
        rows = self.storage.load_scores()
        self.assertEqual(rows["A"]["heavy_ok"], "1")
        self.assertIn("heavy_ts", rows["A"])

    def test_dead_score_not_candidate(self):
        self.sb.rows["A"]["score"] = 0
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["B"])


if __name__ == "__main__":
    unittest.main()
