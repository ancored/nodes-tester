"""Экономия трафика: прогон по сроку ротации — только ноды групп с наступившей
ротацией; карантин слабых нод (cooldown.low_score)."""

import time
import unittest
from unittest.mock import patch

from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
U = "DEMO-vless-us-out [bbbb0001]"
NEW = "DEMO-vless-fr-out [cccc0001]"


def _row(node, region, score=70.0, samples=5, active=0):
    return {"node": node, "region": region, "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": active, "samples": samples, "heavy_ok": "", "heavy_ts": 0}


class ScopedPassTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), tags=[A, B, U, NEW])
        r = self.r
        r._tests_cache = {}
        r.api.all_proxies = lambda: {
            "eu-auto-out": {"all": ["eu-auto-out-failsafe", A, B]},
            "us-auto-out": {"all": ["us-auto-out-failsafe", U]},
        }
        r.board.rows = {A: _row(A, "eu"), B: _row(B, "eu"), U: _row(U, "us")}
        now = time.time()
        r.switcher.state = {
            "eu": {"active": A, "rotate_deadline": now - 1, "last_switch": now - 7200},
            "us": {"active": U, "rotate_deadline": now + 3600, "last_switch": now - 7200},
        }
        self.tested, self.evaluated = [], []

    def tearDown(self):
        self.r.storage.close()

    def _pass(self):
        r = self.r

        def fake_test(region, ident, pass_no, tests, params):
            self.tested.append(ident.raw)
            return {"round": pass_no, "id": ident.node_id, "tests": {}}

        with patch.object(r, "_test_node", side_effect=fake_test), \
                patch.object(r, "_respect_host_gap"), \
                patch.object(r, "_score_and_maybe_switch", return_value=True), \
                patch.object(r, "_run_required_pass"), patch.object(r, "_run_heavy_pass"), \
                patch.object(r.switcher, "evaluate_all",
                             side_effect=lambda groups=None: self.evaluated.append(groups)):
            self.assertFalse(r._run_pass(1))

    def test_rotation_pass_tests_only_due_groups(self):
        self.r._scoped_next = True
        self._pass()
        self.assertEqual(sorted(self.tested), sorted([A, B, NEW]))   # новая нода — всегда
        self.assertIn(U, self.r.board.rows)                          # строка не удалена
        self.assertEqual(self.evaluated, [{"eu"}])
        self.assertFalse(self.r._scoped_next)

    def test_regular_pass_tests_all(self):
        self._pass()
        self.assertEqual(sorted(self.tested), sorted([A, B, U, NEW]))
        self.assertEqual(self.evaluated, [None])

    def test_no_due_group_tests_all(self):
        self.r.switcher.state["eu"]["rotate_deadline"] = time.time() + 3600
        self.r._scoped_next = True
        self._pass()
        self.assertEqual(len(self.tested), 4)

    def test_wait_for_rotation_marks_scoped(self):
        self.r._wait_for_rotation()                  # срок eu уже наступил
        self.assertTrue(self.r._scoped_next)


class LowScoreQuarantineTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), cooldown={"enabled": True, "low_score": 40})
        self.r.board.rows = {A: _row(A, "eu", score=30), B: _row(B, "eu", score=60)}

    def tearDown(self):
        self.r.storage.close()

    def test_low_score_detection(self):
        r = self.r
        self.assertTrue(r._low_score(A))
        self.assertFalse(r._low_score(B))
        r.board.rows[A]["samples"] = 2               # новая нода
        self.assertFalse(r._low_score(A))
        r.board.rows[A].update(samples=5, active=1)  # активная
        self.assertFalse(r._low_score(A))
        r.cfg.cooldown.low_score = 0                 # выключено
        r.board.rows[A]["active"] = 0
        self.assertFalse(r._low_score(A))

    def test_quarantine_and_release(self):
        r = self.r
        r._backoff_update("aaaa0001", gate=True, low=True)
        until, _p, streak, reason = r._backoff["aaaa0001"]
        self.assertEqual(reason, "garbage")
        self.assertAlmostEqual((until - time.time()) / 3600, r.cfg.cooldown.garbage_hours, delta=0.1)
        self.assertTrue(r._backed_off("aaaa0001"))
        events = r.storage._db.execute(
            "SELECT event, reason FROM node_events WHERE crc='aaaa0001'").fetchall()
        self.assertEqual(events, [("garbage", "low-score")])
        r._backoff["aaaa0001"] = (int(time.time()) - 1, 0, streak, "garbage")   # срок вышел
        self.assertFalse(r._backed_off("aaaa0001"))                             # проба
        r._backoff_update("aaaa0001", gate=True, low=False)                     # поднялась
        self.assertNotIn("aaaa0001", r.storage.load_backoff())


class AlignedRotationTest(unittest.TestCase):
    """rotation.align: сроки групп — на общей сетке слотов."""

    def _sw(self, state=None, **rot):
        from nodes_tester.config import RotationConfig, SwitchingConfig
        from nodes_tester.switcher import Switcher
        cfg = SwitchingConfig(enabled=True, rotation=RotationConfig(**rot))
        storage = type("S", (), {"load_switch_state": lambda self: dict(state or {})})()
        return Switcher(cfg, None, _Board(), "nodes-tester", storage=storage)

    def test_switches_at_different_times_share_deadline(self):
        sw = self._sw()
        base = sw._slot(165_740) + 60                 # сразу после слота (ротация)
        a = sw._next_deadline(base)
        b = sw._next_deadline(base + 1200)            # другая группа на 20 мин позже
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, base + 1200 + 1800)   # не раньше min_dwell
        self.assertLessEqual(a - base, 10800 + 1800 + 900 * 2)

    def test_jitter_common_and_bounded(self):
        sw = self._sw()
        for k in range(100, 120):
            self.assertLessEqual(abs(sw._slot(k) - k * 10800), 900)
        self.assertEqual(sw._slot(7), self._sw()._slot(7))   # стабилен между рестартами

    def test_no_align_keeps_per_group_delay(self):
        sw = self._sw(align=False, jitter=0)
        self.assertEqual(sw._next_deadline(1000.0), 1000.0 + 10800)

    def test_loaded_deadlines_snap_to_grid(self):
        now = time.time()
        state = {
            "eu": {"active": A, "rotate_deadline": now + 3600, "last_switch": now - 7200},
            "us": {"active": U, "rotate_deadline": now + 5400, "last_switch": now - 5400},
            "ai": {"active": B, "rotate_deadline": now - 10, "last_switch": now - 9000},
        }
        sw = self._sw(state)
        for g in ("eu", "us"):
            d = sw.state[g]["rotate_deadline"]
            self.assertEqual(d, sw._slot_at_or_after(d))           # на сетке
            self.assertGreaterEqual(d, sw.state[g]["last_switch"] + 1800)
        self.assertEqual(sw.state["ai"]["rotate_deadline"], now - 10)   # наступивший не трогаем


class _Board:
    def set_active(self, group, node):
        pass


if __name__ == "__main__":
    unittest.main()
