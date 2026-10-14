"""Пауза по времени (1, 2, 4, … сроков connectivity) → карантин; снятие; запись в БД."""

import time
import unittest

from tests.helpers import make_runner, temp_dir

HOUR = 3600


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), tests={"connectivity": {"ttl": "1h"}},
                             selection={"quarantine": {"after_skips": 8, "duration": "72h"}})

    def tearDown(self):
        self.r.storage.close()

    def _pause_hours(self, crc):
        return round((self.r._backoff[crc][0] - time.time()) / HOUR)

    def test_pause_doubles_then_quarantine(self):
        r = self.r
        pauses = []
        for _ in range(3):
            r._backoff_update("n", gate=False)
            pauses.append(self._pause_hours("n"))
        self.assertEqual(pauses, [1, 2, 4])
        self.assertEqual(r._backoff["n"][1:], (3, "backoff"))

        r._backoff_update("n", gate=False)                  # 2^3 = 8 = max_skip → карантин
        self.assertEqual(r._backoff["n"][1:], (4, "garbage"))
        self.assertEqual(self._pause_hours("n"), 72)
        self.assertTrue(r._backed_off("n"))

    def test_failed_probe_after_quarantine_returns_to_quarantine(self):
        r = self.r
        r._backoff["n"] = (int(time.time()) - 1, 4, "garbage")   # карантин истёк
        self.assertFalse(r._backed_off("n"))                     # проба
        r._backoff_update("n", gate=False)
        self.assertEqual(r._backoff["n"][2], "garbage")

    def test_backed_off_until_time_and_clear_on_gate(self):
        r = self.r
        r._backoff_update("n", gate=False)
        self.assertTrue(r._backed_off("n"))
        self.assertFalse(r._backed_off("unknown"))
        r._backoff["n"] = (int(time.time()) - 1, 1, "backoff")   # пауза истекла
        self.assertFalse(r._backed_off("n"))
        r._backoff_update("n", gate=True)                         # ожила
        self.assertNotIn("n", r.storage.load_backoff())

    def test_persists_across_reload(self):
        r = self.r
        r._backoff_update("n", gate=False)
        again = r.storage.load_backoff()          # после рестарта
        self.assertEqual(again["n"], r._backoff["n"])


if __name__ == "__main__":
    unittest.main()
