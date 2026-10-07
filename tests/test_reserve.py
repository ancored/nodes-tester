"""Резерв групп: добор проверенных кандидатов, сроки проверок, снятие ограничения по
одной ноде при нехватке, выбор переключателя только из резерва."""

import time
import unittest
from unittest.mock import patch

from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
C, D = "DEMO-vless-fr-out [aaaa0003]", "DEMO-vless-se-out [aaaa0004]"
E = "DEMO-vless-pl-out [aaaa0005]"
U = "DEMO-vless-us-out [bbbb0001]"
EU = [A, B, C, D, E]


def _row(node, region="eu", score=70.0):
    return {"node": node, "region": region, "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": 0, "samples": 5, "heavy_ok": "", "heavy_ts": 0}


class _Base(unittest.TestCase):
    def setUp(self):
        self.r = r = make_runner(temp_dir(), cooldown={"enabled": True, "garbage_hours": 72})
        r._pass_seq = 100
        r.board.set_groups({"eu": set(EU), "us": {U}})
        scores = {A: 95, B: 90, C: 85, D: 80, E: 75}
        r.board.rows = {n: _row(n, score=s) for n, s in scores.items()}
        r.board.rows[U] = _row(U, "us")
        r._node_by_raw = {n: (row["region"], parse_node(n)) for n, row in r.board.rows.items()}
        r.board.set_heavy_ttl(6 * 3600)
        now = time.time()
        st = {"rotate_deadline": now + 3600, "last_switch": now - 7200}
        r.switcher.state = {"eu": {**st, "active": A, "recent": [A]},
                            "us": {**st, "active": U, "recent": [U]}}
        r.switcher._activate = self._fake_activate
        self.activated = []
        self.checks = []
        self.failing = set()                    # (node, проверка), которые проваливаются
        r.check_node = self._fake_check

    def tearDown(self):
        self.r.storage.close()

    def _fake_check(self, raw, check):
        self.checks.append((raw, check))
        ok = (raw, check) not in self.failing
        if check == "heavy":
            self.r.board.set_heavy(raw, ok)
        else:
            self.r.board.set_required_result(raw, check, ok)

    def _fake_activate(self, cand, region, st, now, reason):
        self.activated.append((region, cand["node"], reason))
        st["active"] = cand["node"]
        st["recent"] = [cand["node"]] + st.get("recent", [])
        return True


class KeeperTest(_Base):
    def test_fills_by_score_heavy_only_without_required(self):
        self.r.reserve.maintain("eu")
        self.assertEqual(self.r.board.reserve("eu"), [B, C, D])        # A — активная
        self.assertEqual(self.checks, [(B, "heavy"), (C, "heavy"), (D, "heavy")])

    def test_required_before_heavy_and_failure_skips(self):
        self.r.board.set_required({"eu": {"gemini": 3600, "openai": 3600}})
        self.failing = {(B, "gemini"), (C, "heavy")}
        self.r.reserve.maintain("eu")
        self.assertEqual(self.r.board.reserve("eu"), [D, E])
        self.assertEqual(self.checks[:4], [(B, "gemini"), (C, "gemini"), (C, "openai"), (C, "heavy")])
        self.assertNotIn((B, "openai"), self.checks)                  # провал — дальше не гоним

    def test_fresh_results_not_repeated(self):
        self.r.board.set_heavy(B, True)
        self.r.reserve.maintain("eu")
        self.assertNotIn((B, "heavy"), self.checks)
        self.assertIn(B, self.r.board.reserve("eu"))

    def test_avoid_recent_excluded_and_refill_after_activation(self):
        self.r.switcher.state["eu"]["recent"] = [A, B]
        self.r.reserve.maintain("eu")
        self.assertEqual(self.r.board.reserve("eu"), [C, D, E])
        self.r.switcher.evaluate_region("eu", emergency=True)        # авария → из резерва
        region, node, reason = self.activated[-1]
        self.assertEqual((region, reason), ("eu", "emergency"))
        self.assertIn(node, (C, D, E))
        self.r.reserve.maintain("eu")
        self.assertNotIn(node, self.r.board.reserve("eu"))
        self.assertIn(B, self.r.board.reserve("eu"))                  # B выпала из двух последних

    def test_expired_member_retested_and_dropped_on_failure(self):
        self.r.reserve.maintain("eu")
        self.checks.clear()
        self.r.board.rows[B]["heavy_ts"] = int(time.time()) - 7 * 3600   # срок heavy истёк
        self.failing = {(B, "heavy")}
        self.r.reserve.maintain("eu")
        self.assertEqual(self.checks, [(B, "heavy"), (E, "heavy")])
        self.assertEqual(self.r.board.reserve("eu"), [C, D, E])

    def test_inconclusive_does_not_admit(self):
        self.r.check_node = lambda raw, check: self.checks.append((raw, check))   # без вердикта
        self.r.reserve.maintain("eu")
        self.assertEqual(self.r.board.reserve("eu"), [])
        self.assertEqual(len(self.checks), 4)                         # по разу на каждую ноду

    def test_failsafe_left_when_reserve_appears(self):
        self.r.board.set_required({"eu": {"gemini": 3600}})
        self.r.switcher.state["eu"].update(active=None, failsafe=True)
        self.r.reserve.maintain("eu")
        self.assertEqual(self.activated[0][1:], (B, "init"))


class ReleaseTest(_Base):
    def setUp(self):
        super().setUp()
        r = self.r
        later = int(time.time()) + 3600
        r.storage.set_backoff("aaaa0004", later, 5, "garbage")
        r.storage.set_backoff("aaaa0005", None, 2, "backoff", until_pass=103)
        r.storage.set_backoff("bbbb0001", later, 5, "garbage")
        r.storage._db.execute("UPDATE garbage SET since = 1000 WHERE crc = 'aaaa0005'")
        r.storage._db.commit()
        r._backoff = r.storage.load_backoff()
        r.board.set_restricted(r._restricted_now())
        self.gate = {}
        self.light = []

        def fake_test(region, ident, pass_no, tests, params):
            self.light.append(ident.raw)
            return {"tests": {}}

        r._test_node = fake_test
        r._score_and_maybe_switch = lambda region, ident, record: self.gate.get(ident.raw, True)

    def test_oldest_released_one_by_one(self):
        r = self.r
        self.assertEqual(r.release_for_reserve("eu", set()), E)      # с паузой дольше всех
        self.assertFalse(r._backed_off("aaaa0005"))
        self.assertEqual(r.release_for_reserve("eu", {E}), D)
        self.assertIsNone(r.release_for_reserve("eu", {E, D}))       # чужую группу не трогаем
        self.assertTrue(r._backed_off("bbbb0001"))
        events = r.storage._db.execute(
            "SELECT crc, event, reason FROM node_events WHERE event = 'restriction_cleared'"
            " ORDER BY crc").fetchall()
        self.assertEqual(events, [("aaaa0004", "restriction_cleared", "reserve"),
                                  ("aaaa0005", "restriction_cleared", "reserve")])

    def test_failed_recheck_returns_to_quarantine_and_waits(self):
        r = self.r
        self.gate[D] = False
        r.storage._db.execute("UPDATE garbage SET since = 1 WHERE crc = 'aaaa0004'")
        r.storage._db.commit()
        self.assertEqual(r.release_for_reserve("eu", set()), D)
        until, _p, streak, reason = r._backoff["aaaa0004"]
        self.assertEqual((reason, streak), ("garbage", 6))
        self.assertGreater(until, time.time() + 3600)
        self.assertEqual(r.release_for_reserve("eu", set()), E)      # D повторно не снимаем
        self.assertIsNone(r.release_for_reserve("eu", {E}))

    def test_banned_not_released(self):
        r = self.r
        r.storage.banned_crcs = lambda: {"aaaa0004", "aaaa0005"}
        self.assertIsNone(r.release_for_reserve("eu", set()))

    def test_keeper_releases_when_candidates_run_out(self):
        r = self.r
        r.switcher.state["eu"]["recent"] = [B, C]                     # с активной A — все трое
        r.reserve.maintain("eu")
        self.assertEqual(self.light, [E, D])
        self.assertEqual(r.board.reserve("eu"), [E, D])
        self.assertIn("eu", r.reserve._retry_at)                      # 2 из 3 — пауза добора
        self.light.clear()
        r.reserve.maintain("eu")
        self.assertEqual(self.light, [])
        r.reserve.wake(ratings_changed=True)                          # новый проход — сразу
        self.assertNotIn("eu", r.reserve._retry_at)


class SwitcherFromReserveTest(_Base):
    def test_rotation_only_from_reserve(self):
        r = self.r
        r.switcher.state["eu"]["rotate_deadline"] = time.time() - 1
        r.switcher.evaluate_region("eu")
        self.assertEqual(self.activated, [])                          # резерв пуст — ждём
        self.assertGreater(r.switcher.state["eu"]["rotate_deadline"], time.time())  # срок перенесён
        r.switcher.state["eu"]["rotate_deadline"] = time.time() - 1
        r.board.set_reserve("eu", [D])
        r.switcher.evaluate_region("eu")
        self.assertEqual(self.activated, [("eu", D, "rotation")])

    def test_quality_only_to_reserve(self):
        r = self.r
        r.board.rows[A]["score"] = 40                                 # ниже comfort_floor
        with patch.object(r.switcher.cfg, "confirm_cycles", 1):
            r.switcher.evaluate_region("eu")
            self.assertEqual(self.activated, [])
            r.board.set_reserve("eu", [C])
            r.switcher.evaluate_region("eu")
        self.assertEqual(self.activated, [("eu", C, "quality")])

    def test_emergency_prefers_reserve_falls_back_to_candidates(self):
        r = self.r
        r.board.set_reserve("eu", [E])
        r.switcher.evaluate_region("eu", emergency=True)
        self.assertEqual(self.activated[-1], ("eu", E, "emergency"))
        r.board.set_reserve("eu", [])
        r.switcher.evaluate_region("eu", emergency=True)
        self.assertEqual(self.activated[-1][2], "emergency")
        self.assertNotEqual(self.activated[-1][1], E)


if __name__ == "__main__":
    unittest.main()
