"""Дашборд: pass_label берёт pass_no из строки с максимальным ts (не независимый
max), иначе через полночь свежая дата склеивается со старым номером прогона."""

import time
import unittest

from dashboard import data as D


class PassLabelTest(unittest.TestCase):
    def _run(self, rows):
        orig = D._query
        D._query = lambda db, sql: rows
        try:
            return D._results("nodb")
        finally:
            D._query = orig

    def _row(self, test, ts, pass_no):
        return {"crc": "z", "test": test, "ok": 1, "metrics": "{}", "error": "",
                "ts": ts, "pass_no": pass_no, "provider": "P", "protocol": "vless",
                "country": "nl"}

    def test_pass_no_from_latest_ts(self):
        now = int(time.time())
        rows = [self._row("connectivity", now - 90000, 15),   # вчера, большой номер
                self._row("latency", now, 2)]                 # сегодня, свежий
        res = self._run(rows)
        node = res["rows"][0]
        self.assertEqual(node["pass_no"], 2)
        self.assertTrue(node["pass_label"].endswith("-002"))

    def test_single_row(self):
        now = int(time.time())
        res = self._run([self._row("connectivity", now, 7)])
        self.assertEqual(res["rows"][0]["pass_no"], 7)


if __name__ == "__main__":
    unittest.main()
