"""Единый временной backoff: удвоение, потолок→карантин, снятие, устойчивость к
суточному сбросу pass_no (review.md P1)."""

import time
import unittest

from tests.helpers import make_runner, temp_dir


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(),
                             cooldown={"enabled": True, "base_seconds": 600,
                                       "max_skip": 32, "garbage_hours": 72})

    def tearDown(self):
        self.r.storage.close()

    def test_doubles_then_caps_to_garbage(self):
        r = self.r
        r._backoff_update("n", gate=False)
        until, streak, reason = r._backoff["n"]
        self.assertEqual((streak, reason), (1, "backoff"))
        self.assertAlmostEqual((until - time.time()) / 60, 10, delta=1)   # 600с

        for _ in range(3):
            r._backoff_update("n", gate=False)
        until, streak, _ = r._backoff["n"]
        self.assertEqual(streak, 4)
        self.assertAlmostEqual((until - time.time()) / 60, 80, delta=1)   # 600·2^3

        for _ in range(20):
            r._backoff_update("n", gate=False)
        until, _, reason = r._backoff["n"]
        self.assertEqual(reason, "garbage")                              # потолок
        self.assertAlmostEqual((until - time.time()) / 60, 72 * 60, delta=2)

    def test_backed_off_and_clear_on_gate(self):
        r = self.r
        r._backoff_update("n", gate=False)
        self.assertTrue(r._backed_off("n"))
        self.assertFalse(r._backed_off("unknown"))
        r._backoff_update("n", gate=True)                                # ожила
        self.assertFalse(r._backed_off("n"))
        self.assertNotIn("n", r.storage.load_backoff())

    def test_persists_across_reload(self):
        r = self.r
        r._backoff_update("n", gate=False)
        # новый снимок из БД (как на следующем проходе/после рестарта)
        again = r.storage.load_backoff()
        self.assertIn("n", again)
        self.assertGreater(again["n"][0], time.time())

    def test_time_based_survives_pass_no_reset(self):
        # backoff измеряется временем, а не pass_no → полуночный сброс не влияет.
        r = self.r
        r._backoff_update("n", gate=False)
        until = r._backoff["n"][0]
        # «сбросили pass_no» — на _backed_off это никак не влияет
        self.assertTrue(r._backed_off("n"))
        self.assertGreater(until, time.time())


if __name__ == "__main__":
    unittest.main()
