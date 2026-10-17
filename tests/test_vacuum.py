"""VACUUM по расписанию storage.vacuum: срок, отсчёт от первого запуска, итог в meta."""

import datetime as dt
import os
import sqlite3
import unittest

from nodes_tester.config import VacuumConfig
from nodes_tester.storage import Storage
from nodes_tester.vacuum import Vacuum, next_due
from tests.helpers import temp_dir


class _Cfg:
    def __init__(self, db):
        self.path = db
        self.retention = 30 * 86400
        self.retention_days = 30


def _ts(*args):
    return dt.datetime(*args).timestamp()


class NextDueTest(unittest.TestCase):
    def test_first_slot_after_the_interval(self):
        last = _ts(2026, 10, 1, 4, 0)
        self.assertEqual(next_due(last, 30 * 86400, "04:00"), _ts(2026, 10, 31, 4, 0))
        self.assertEqual(next_due(last, 30 * 86400, "03:00"), _ts(2026, 11, 1, 3, 0))
        self.assertEqual(next_due(_ts(2026, 10, 1, 4, 30), 86400, "04:00"), _ts(2026, 10, 3, 4, 0))


class VacuumTest(unittest.TestCase):
    def setUp(self):
        self.now = _ts(2026, 10, 1, 12, 0)
        self.st = Storage(_Cfg(os.path.join(temp_dir(), "s.db")))
        self.vacuum = Vacuum(self.st, VacuumConfig(every=30 * 86400, at="04:00"), clock=lambda: self.now)

    def tearDown(self):
        self.st.close()

    def test_schedule_counts_from_the_first_start(self):
        self.assertIsNone(self.vacuum.state()["last"])
        self.assertEqual(self.vacuum.next(), _ts(2026, 11, 1, 4, 0))
        self.assertFalse(self.vacuum.due(_ts(2026, 10, 31, 23, 0)))
        self.assertTrue(self.vacuum.due(_ts(2026, 11, 1, 4, 0)))
        # Перезапуск тестера отсчёт не сдвигает.
        again = Vacuum(self.st, VacuumConfig(every=30 * 86400, at="04:00"), clock=lambda: self.now + 86400)
        self.assertEqual(again.next(), _ts(2026, 11, 1, 4, 0))

    def test_run_records_sizes_and_moves_the_schedule(self):
        self.st._db.execute("CREATE TABLE junk (x BLOB)")
        self.st._db.executemany("INSERT INTO junk VALUES (?)", [(b"x" * 4096,)] * 200)
        self.st._db.execute("DROP TABLE junk")
        self.st._db.commit()
        self.assertTrue(self.vacuum.start("manual"))
        self.vacuum._thread.join(10)
        last = self.vacuum.state()["last"]
        self.assertEqual(last["trigger"], "manual")
        self.assertLess(last["size_after"], last["size_before"])
        self.assertEqual(self.vacuum.next(), _ts(2026, 11, 1, 4, 0))   # 12:00 + 30 сут → ближайшие 04:00

    def test_failure_is_retried_next_day(self):
        def fail():
            raise sqlite3.OperationalError("database is locked")
        self.st.vacuum = fail
        self.vacuum.start("schedule")
        self.vacuum._thread.join(10)
        self.assertEqual(self.vacuum.state()["last"]["error"], "database is locked")
        self.assertEqual(self.vacuum.next(), _ts(2026, 10, 3, 4, 0))

    def test_manual_only(self):
        manual = Vacuum(self.st, VacuumConfig(every=0, at="04:00"))
        self.assertIsNone(manual.next())
        self.assertFalse(manual.due(_ts(2030, 1, 1)))


if __name__ == "__main__":
    unittest.main()
