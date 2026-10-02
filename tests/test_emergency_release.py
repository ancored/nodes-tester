"""Авария без замены: досрочное снятие паузы и карантина с нод группы и внеплановая
проверка только этой группы."""

import time
import unittest

from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
C = "DEMO-vless-fr-out [aaaa0003]"
U = "DEMO-vless-us-out [bbbb0001]"


def _row(node, region, score=70.0):
    return {"node": node, "region": region, "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": 0, "samples": 5, "heavy_ok": "", "heavy_ts": 0}


class EmergencyReleaseTest(unittest.TestCase):
    def setUp(self):
        self.r = r = make_runner(temp_dir(), cooldown={"enabled": True, "garbage_hours": 72})
        r._pass_seq = 100
        r.board.set_groups({"eu": {A, B, C}, "us": {U}})
        r.board.rows = {A: _row(A, "eu", 0.0), B: _row(B, "eu"), C: _row(C, "eu"),
                        U: _row(U, "us")}
        later = int(time.time()) + 3600
        r.storage.set_backoff("aaaa0002", later, 5, "garbage")
        r.storage.set_backoff("aaaa0003", None, 2, "backoff", until_pass=103)
        r.storage.set_backoff("bbbb0001", later, 5, "garbage")
        r._backoff = r.storage.load_backoff()
        r.board.set_restricted(r._restricted_now())
        st = {"rotate_deadline": time.time() + 3600, "last_switch": time.time() - 7200}
        r.switcher.state = {"eu": {**st, "active": A}, "us": {**st, "active": U}}

    def tearDown(self):
        self.r.storage.close()

    def test_stuck_releases_group_and_requests_scoped_pass(self):
        r = self.r
        r.switcher.evaluate_region("eu", emergency=True)     # замены нет → emergency-stuck
        self.assertTrue(r.switcher.stuck("eu"))
        self.assertFalse(r._backed_off("aaaa0002"))           # карантин снят досрочно
        self.assertFalse(r._backed_off("aaaa0003"))           # пауза тоже
        self.assertTrue(r._backed_off("bbbb0001"))            # чужая группа не тронута
        self.assertEqual(r.storage.load_backoff()["aaaa0002"][2:], (5, "garbage"))
        self.assertTrue(r._pass_requested.is_set())
        self.assertTrue(r._scoped_next)
        self.assertEqual(r._due_groups(), {"eu"})
        events = r.storage._db.execute(
            "SELECT crc, event, reason FROM node_events ORDER BY crc").fetchall()
        self.assertEqual(events, [("aaaa0002", "restriction_cleared", "emergency-stuck"),
                                  ("aaaa0003", "restriction_cleared", "emergency-stuck")])

    def test_failed_recheck_returns_to_quarantine(self):
        r = self.r
        r._release_group("eu")
        r._backoff_update("aaaa0002", gate=False)
        until, _p, _s, reason = r._backoff["aaaa0002"]
        self.assertEqual(reason, "garbage")
        self.assertGreater(until, time.time() + 3600)

    def test_banned_not_released(self):
        r = self.r
        r.storage.banned_crcs = lambda: {"aaaa0002"}
        r._release_group("eu")
        self.assertTrue(r._backed_off("aaaa0002"))

    def test_admin_request_keeps_full_pass(self):
        r = self.r
        r.request_pass()
        r._release_group("eu")
        self.assertFalse(r._scoped_next)
        r._pass_requested.clear()
        r._release_group("eu")                     # повторно снимать нечего
        self.assertFalse(r._pass_requested.is_set())

    def test_admin_request_after_release_is_full(self):
        r = self.r
        r._release_group("eu")
        r.request_pass()
        self.assertFalse(r._scoped_next)


if __name__ == "__main__":
    unittest.main()
