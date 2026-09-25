"""Пауза в прогонах (1, 2, 4, …) → карантин во времени; снятие; сквозной счётчик
прогонов не сбрасывается в полночь (review.md P1)."""

import time
import unittest

from tests.helpers import make_runner, temp_dir


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(),
                             cooldown={"enabled": True, "max_skip": 8, "garbage_hours": 72})
        self.r._pass_seq = 100

    def tearDown(self):
        self.r.storage.close()

    def _skipped_passes(self, crc):
        """Сколько следующих прогонов нода пропустит (двигаем сквозной счётчик)."""
        r, n = self.r, 0
        start = r._pass_seq
        while True:
            r._pass_seq += 1
            if not r._backed_off(crc):
                break
            n += 1
        r._pass_seq = start
        return n

    def test_skips_double_then_quarantine(self):
        r = self.r
        skips = []
        for _ in range(3):
            r._backoff_update("n", gate=False)
            skips.append(self._skipped_passes("n"))
        self.assertEqual(skips, [1, 2, 4])
        until, until_pass, streak, reason = r._backoff["n"]
        self.assertEqual((streak, reason, until_pass), (3, "backoff", 104))

        r._backoff_update("n", gate=False)                  # 2^3 = 8 = max_skip → карантин
        until, _, streak, reason = r._backoff["n"]
        self.assertEqual((streak, reason), (4, "garbage"))
        self.assertAlmostEqual((until - time.time()) / 3600, 72, delta=0.1)
        r._pass_seq += 1000                                 # прогоны не снимают карантин
        self.assertTrue(r._backed_off("n"))

    def test_failed_probe_after_quarantine_returns_to_quarantine(self):
        r = self.r
        r._backoff["n"] = (int(time.time()) - 1, 0, 4, "garbage")   # карантин истёк
        self.assertFalse(r._backed_off("n"))                        # проба
        r._backoff_update("n", gate=False)
        self.assertEqual(r._backoff["n"][3], "garbage")

    def test_backed_off_and_clear_on_gate(self):
        r = self.r
        r._backoff_update("n", gate=False)
        r._pass_seq += 1
        self.assertTrue(r._backed_off("n"))
        self.assertFalse(r._backed_off("unknown"))
        r._backoff_update("n", gate=True)                                # ожила
        self.assertFalse(r._backed_off("n"))
        self.assertNotIn("n", r.storage.load_backoff())

    def test_persists_across_reload(self):
        r = self.r
        r._backoff_update("n", gate=False)
        again = r.storage.load_backoff()          # как на следующем проходе/после рестарта
        self.assertEqual(again["n"][1:], (101, 1, "backoff"))

    def test_pass_seq_persists_and_is_monotonic(self):
        # _run_pass поднимает сквозной счётчик из meta и не зависит от суточного pass_no.
        r = self.r
        r.storage.set_meta("pass_seq", 500)
        r._pass_seq = 0
        r.clash.list_group_members = lambda g: []          # пустой прогон
        r._run_pass(1)                                     # посуточный номер 1 («после полуночи»)
        self.assertEqual(r._pass_seq, 501)
        self.assertEqual(r.storage.get_meta("pass_seq"), "501")

    def test_legacy_time_based_row_probes_next_pass(self):
        r = self.r
        r.storage.set_backoff("old", int(time.time()) + 3600, 3, "backoff")   # старая модель
        r._backoff = r.storage.load_backoff()
        self.assertFalse(r._backed_off("old"))             # until_pass=0 → пробуем сразу
        self.assertEqual(r._backoff["old"][2], 3)          # streak сохранён


if __name__ == "__main__":
    unittest.main()
