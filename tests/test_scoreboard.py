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

    def test_restricted_node_not_candidate_despite_score(self):
        # Карантин/пауза/бан сохраняют строку и рейтинг, но выбирать такую ноду нельзя.
        self.sb.restrict("A", True)
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["B"])
        self.assertEqual(self.sb.probe_pool("eu"), ["B"])
        self.assertTrue(self.sb.is_restricted("A"))
        self.sb.restrict("A", False)
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["A", "B"])
        self.sb.set_restricted({"B"})
        self.assertEqual([c["node"] for c in self.sb.candidates("eu")], ["A"])


class ActiveFlagTest(unittest.TestCase):
    """Флаг active — «активна хотя бы в одной группе», в том числе до загрузки состава групп."""

    def setUp(self):
        self.storage = Storage(StorageConfig(db_file=os.path.join(temp_dir(), "s.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.rows = {"A": _row("A", region="eu"), "B": _row("B", region="eu")}

    def tearDown(self):
        self.storage.close()

    def test_group_named_not_as_region_keeps_flag(self):
        # Как при старте Switcher: состав групп ещё неизвестен, ai-нода с регионом eu.
        self.sb.set_active("eu", "A")
        self.sb.set_active("us", "X")
        self.sb.set_active("ai", "B")
        self.assertEqual((self.sb.rows["A"]["active"], self.sb.rows["B"]["active"]), (1, 1))
        self.sb.set_active("ai", None)
        self.assertEqual((self.sb.rows["A"]["active"], self.sb.rows["B"]["active"]), (1, 0))

    def test_switcher_start_writes_active_flags_to_db(self):
        from nodes_tester.config import SwitchingConfig
        from nodes_tester.switcher import Switcher
        self.sb.write()
        self.storage.save_switch_state({"eu": {"active": "eu-node [aaaaaaaa]"},
                                        "ai": {"active": "ai-node [bbbbbbbb]"}})
        self.storage.save_scores([dict(_row("eu-node [aaaaaaaa]"), id="aaaaaaaa"),
                                  dict(_row("ai-node [bbbbbbbb]"), id="bbbbbbbb")])
        Switcher(SwitchingConfig(enabled=True), None, self.sb, "nodes-tester", storage=self.storage)
        rows = self.storage.load_scores()
        self.assertEqual(sorted(r["id"] for r in rows.values() if r["active"]), ["aaaaaaaa", "bbbbbbbb"])


class RestrictedActiveTest(unittest.TestCase):
    """Активная нода, попавшая под ограничение, заменяется немедленно (emergency)."""

    def setUp(self):
        self.storage = Storage(StorageConfig(db_file=os.path.join(temp_dir(), "s.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.rows = {"A": _row("A", score=80), "B": _row("B", score=60)}

    def tearDown(self):
        self.storage.close()

    def test_restricted_active_replaced(self):
        sw = _switcher(self.sb, {"eu": {"active": "A", "last_switch": 0,
                                        "rotate_deadline": 4e9}})
        picked = []
        sw._activate = lambda cand, region, st, now, reason: picked.append(
            (cand["node"], reason)) or True
        sw.evaluate_region("eu")
        self.assertEqual(picked, [])                 # активная в порядке — не трогаем
        self.sb.restrict("A", True)
        sw.evaluate_region("eu")
        self.assertEqual(picked, [("B", "emergency")])


class _Ident:
    def __init__(self, raw):
        self.raw = raw


def _switcher(board, state=None):
    from nodes_tester.config import SwitchingConfig
    from nodes_tester.switcher import Switcher
    sw = Switcher(SwitchingConfig(enabled=True), None, board, "nodes-tester")
    sw.state = state or {}
    return sw


class HeavyBeforeRotationTest(unittest.TestCase):
    """heavy_download — весь пул ротации, только в регионах, где наступает ротация."""

    def setUp(self):
        import time
        from tests.helpers import make_runner
        self.tmp = temp_dir()
        self.runner = make_runner(self.tmp, run={"default": {"heavy_candidates": 1}})
        self.runner._order_by_host = lambda items: items
        self.storage = Storage(StorageConfig(db_file=os.path.join(self.tmp, "h.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.set_heavy_veto_ttl(3600)
        eu = [("A", 95), ("B", 90), ("C", 85), ("D", 80), ("E", 75), ("F", 70), ("G", 65), ("H", 40)]
        self.sb.rows = {n: _row(n, score=s) for n, s in eu}
        self.sb.rows["U"] = _row("U", region="us", score=90)
        self.sb.rows["V"] = _row("V", region="us", score=80)
        self.runner.board = self.sb
        now = time.time()
        self.state = {
            "eu": {"active": "A", "rotate_deadline": now - 1, "last_switch": now - 7200},
            "us": {"active": "U", "rotate_deadline": now + 3600, "last_switch": now - 7200},
        }
        self.runner.switcher = _switcher(self.sb, self.state)
        self.by_raw = {n: (row["region"], _Ident(n)) for n, row in self.sb.rows.items()}

    def tearDown(self):
        self.storage.close()

    def _targets(self, done=frozenset()):
        return [ident.raw for _region, ident in self.runner._heavy_targets(self.by_raw, done)]

    def test_whole_rotation_pool_of_due_region(self):
        # top_k=5 без активной A; us — срок не наступил
        self.assertEqual(self._targets(), ["B", "C", "D", "E", "F"])

    def test_pool_skips_recent_and_low_score(self):
        self.state["eu"]["recent"] = ["C"]
        self.sb.rows["D"]["score"] = 50                 # ниже min_score 55
        self.assertEqual(self._targets(), ["B", "E", "F", "G"])

    def test_no_active_checks_init_choice(self):
        self.state["us"] = {}
        self.assertIn("U", self._targets())
        self.assertNotIn("V", self._targets())

    def test_min_dwell_blocks(self):
        import time
        self.state["eu"]["last_switch"] = time.time() - 60
        self.assertEqual(self._targets(), [])

    def test_fresh_heavy_not_repeated(self):
        import time
        self.sb.set_heavy("B", True)                     # свежий успех — не перекачиваем
        self.sb.rows["C"].update(heavy_ok="1", heavy_ts=int(time.time()) - 4 * 3600)  # старше ротации
        self.assertEqual(self._targets(), ["C", "D", "E", "F"])

    def test_no_switcher_no_heavy(self):
        self.runner.switcher = None
        self.assertEqual(self._targets(), [])

    def test_veto_refills_pool(self):
        rounds = []
        fail = {"C", "D"}

        def fake_round(heavy, targets, pass_no, first):
            rounds.append([ident.raw for _r, ident in targets])
            for _r, ident in targets:
                self.sb.set_heavy(ident.raw, ident.raw not in fail)

        self.runner._heavy_round = fake_round
        self.runner._heavy_test = lambda: object()
        self.runner._run_heavy_pass(self.by_raw, 1)
        self.assertEqual(rounds, [["B", "C", "D", "E", "F"], ["G"]])   # H < min_score
        pick = self.runner.switcher.rotation_pool("eu")
        self.assertEqual(pick, ["B", "E", "F", "G"])


class SwitcherRotationDueTest(unittest.TestCase):
    def test_conditions(self):
        import time
        sw = _switcher(None)
        self.assertTrue(sw.rotation_due("eu"))                  # активной нет — init
        now = time.time()
        sw.state["eu"] = {"active": "A", "rotate_deadline": now + 600, "last_switch": now - 7200}
        self.assertFalse(sw.rotation_due("eu"))                 # срок не наступил
        sw.state["eu"]["rotate_deadline"] = now - 1
        self.assertTrue(sw.rotation_due("eu"))
        sw.state["eu"]["last_switch"] = now - 60
        self.assertFalse(sw.rotation_due("eu"))                 # min_dwell


if __name__ == "__main__":
    unittest.main()
