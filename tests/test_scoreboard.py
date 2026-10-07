"""Рейтинг: heavy-veto как фильтр кандидатов (свежий veto исключает ноду, но при
отсутствии не-vetoed возвращаем vetoed), флаг активной, претенденты в резерв."""

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
        self.sb.set_heavy_ttl(3600)
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
        self.assertEqual(self.sb.reserve_pool("eu"), ["B"])
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


def _switcher(board, state=None):
    from nodes_tester.config import SwitchingConfig
    from nodes_tester.switcher import Switcher
    sw = Switcher(SwitchingConfig(enabled=True), None, board, "nodes-tester")
    sw.state = state or {}
    return sw


class ReservePoolTest(unittest.TestCase):
    """Претенденты в резерв и недостающие проверки ноды."""

    def setUp(self):
        self.storage = Storage(StorageConfig(db_file=os.path.join(temp_dir(), "s.db")))
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.set_heavy_ttl(3600)
        self.sb.rows = {n: _row(n, score=s) for n, s in (("A", 90), ("B", 80), ("C", 50), ("D", 70))}

    def tearDown(self):
        self.storage.close()

    def test_order_by_score_low_score_last_and_vetoed_dropped(self):
        self.sb.set_heavy("D", False)
        self.assertEqual(self.sb.reserve_pool("eu", min_score=55), ["A", "B", "C"])
        self.sb.rows["B"]["score"] = 0
        self.assertEqual(self.sb.reserve_pool("eu", min_score=95), ["A", "C"])

    def test_missing_required_then_heavy(self):
        import time
        self.sb.set_required({"eu": {"gemini": 3600, "openai": 3600}})
        self.assertEqual(self.sb.reserve_missing("A", "eu"), ["gemini", "openai", "heavy"])
        self.sb.set_required_result("A", "gemini", True)
        self.sb.set_heavy("A", True)
        self.assertEqual(self.sb.reserve_missing("A", "eu"), ["openai"])
        self.sb.set_required_result("A", "openai", True)
        self.assertEqual(self.sb.reserve_missing("A", "eu"), [])
        self.sb.rows["A"]["heavy_ts"] = int(time.time()) - 7200          # heavy просрочен
        self.assertEqual(self.sb.reserve_missing("A", "eu"), ["heavy"])
        self.sb.set_required_result("A", "openai", False)
        self.assertIsNone(self.sb.reserve_missing("A", "eu"))            # свежий провал
        self.assertNotIn("A", self.sb.reserve_pool("eu"))

    def test_restricted_and_vetoed_not_usable(self):
        self.sb.restrict("A", True)
        self.assertIsNone(self.sb.reserve_missing("A", "eu"))
        self.sb.set_heavy("B", False)
        self.assertIsNone(self.sb.reserve_missing("B", "eu"))

    def test_reserve_flag_in_db(self):
        self.sb.write()
        self.sb.set_reserve("eu", ["A", "B"])
        self.sb.set_reserve("ai", ["A"])
        rows = {r["id"]: r for r in self.storage.load_scores().values()}
        import sqlite3
        db = sqlite3.connect(self.storage.cfg.db_file)
        marks = dict(db.execute("SELECT crc, reserve FROM scores").fetchall())
        db.close()
        self.assertEqual((marks["A"], marks["B"], marks["C"]), ("ai,eu", "eu", ""))
        self.assertEqual(len(rows), 4)
        self.sb.write()                                   # полная перезапись не теряет флаг
        db = sqlite3.connect(self.storage.cfg.db_file)
        self.assertEqual(db.execute("SELECT reserve FROM scores WHERE crc='A'").fetchone()[0], "ai,eu")
        db.close()


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
