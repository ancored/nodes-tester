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


class HeavyTtlTest(unittest.TestCase):
    """heavy_download не пере-качивается, пока результат моложе heavy_veto_hours."""

    def setUp(self):
        from tests.helpers import make_runner
        self.tmp = temp_dir()
        self.runner = make_runner(self.tmp, run={"default": {"heavy_candidates": 2}})
        self.storage = Storage(StorageConfig(db_file=os.path.join(self.tmp, "h.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.set_heavy_veto_ttl(3600)
        self.sb.rows = {n: _row(n, score=s) for n, s in (("A", 90), ("B", 80), ("C", 70))}
        self.runner.board = self.sb
        self.runner.switcher = None
        self.by_raw = {n: ("eu", n) for n in self.sb.rows}

    def tearDown(self):
        self.storage.close()

    def _targets(self):
        return [ident for _region, ident in self.runner._heavy_targets(self.by_raw)]

    def test_second_pass_within_ttl_skips(self):
        self.assertEqual(self._targets(), ["A", "B"])
        self.sb.set_heavy("A", True)
        self.sb.set_heavy("B", False)
        # A свежий ok, B свежий veto → выбыл из топа, его место занял C
        self.assertEqual(self._targets(), ["C"])
        self.sb.set_heavy("C", True)
        self.assertEqual(self._targets(), [])

    def test_expired_result_retested(self):
        self.sb.set_heavy("A", True)
        self.sb.rows["A"]["heavy_ts"] -= 3601
        self.assertEqual(self._targets(), ["A", "B"])

    def test_ttl_zero_tests_every_pass(self):
        self.sb.set_heavy_veto_ttl(0)
        self.sb.set_heavy("A", True)
        self.assertEqual(self._targets(), ["A", "B"])

    def test_active_node_respects_ttl(self):
        class Sw:
            def active_node(self, region):
                return "C"
        self.runner.switcher = Sw()
        self.assertEqual(self._targets(), ["A", "B", "C"])
        self.sb.set_heavy("C", True)
        self.assertEqual(self._targets(), ["A", "B"])

    def test_ttl_survives_reload(self):
        self.sb.set_heavy("A", True)
        self.sb.write()
        sb2 = Scoreboard(self.storage, ScoringConfig())
        sb2.set_heavy_veto_ttl(3600)
        self.assertTrue(sb2.heavy_fresh("A"))
        self.assertFalse(sb2.heavy_fresh("B"))


if __name__ == "__main__":
    unittest.main()
