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

    def test_heavy_success_has_no_failure_details(self):
        now = int(time.time())
        rows = [self._row("latency", now, 7), self._row("heavy_download", now + 30, 7)]
        cell = self._run(rows)["rows"][0]["cells"]["heavy_download"]
        self.assertEqual(cell["ok"], 1)
        self.assertNotIn("title", cell)

    def test_failure_keeps_service_error(self):
        row = self._row("connectivity", int(time.time()), 7)
        row.update(ok=0, error="connection refused")
        self.assertEqual(self._run([row])["rows"][0]["cells"]["connectivity"]["title"], "connection refused")


class _Cfg:
    def __init__(self, db):
        self.db_file = db
        self.retention_days = 30


class LifecycleGraveyardTest(unittest.TestCase):
    """«Жизненный цикл» — только ноды из подписки; удалённые — на «Кладбище».
    Долгожители — только в строю (score > 0, не пауза/карантин/бан)."""

    def setUp(self):
        import os
        from nodes_tester import storage as S
        from tests.helpers import temp_dir
        self.db = os.path.join(temp_dir(), "s.db")
        self.st = S.Storage(_Cfg(self.db))
        now = int(time.time())
        d = 86400
        x = self.st._db.execute
        # crc, present, banned, first_seen, score, garbage-state
        nodes = [("ok", 1, 0, now - 10 * d, 80, None),
                 ("zero", 1, 0, now - 20 * d, 0, None),
                 ("quar", 1, 0, now - 30 * d, 50, "garbage"),
                 ("paus", 1, 0, now - 5 * d, 0, "backoff"),
                 ("ban", 1, 1, now - 40 * d, 70, None),
                 ("gone", 0, 0, now - 15 * d, None, "garbage")]
        for crc, pres, ban, fs, sc, g in nodes:
            x("INSERT INTO nodes (crc, tag, provider, protocol, country, first_seen, "
              "last_seen, present, banned) VALUES (?,?,?,?,?,?,?,?,?)",
              (crc, crc, "P", "vless", "nl", fs, now - (3 * d if not pres else 0), pres, ban))
            if sc is not None:
                x("INSERT INTO scores (crc, node, score, active) VALUES (?,?,?,0)", (crc, crc, sc))
            if g:
                x("INSERT INTO garbage (crc, since, until, reason, streak) VALUES (?,?,?,?,1)",
                  (crc, now - 3600, now + 3600, g))
                x("INSERT INTO node_events (ts, crc, event) VALUES (?,?,?)",
                  (now - 3600, crc, g))
        for i, v in enumerate([60, 90, 40, 0]):              # траектория удалённой
            x("INSERT INTO score_history (ts, crc, score) VALUES (?,?,?)",
              (now - 10 * d + i * 3600, "gone", v))
        x("INSERT INTO node_events (ts, crc, event) VALUES (?,?,'removed')", (now - 3 * d, "gone"))
        self.st._db.commit()
        self.now, self.d = now, d

    def tearDown(self):
        self.st.close()

    def test_garbage_table_excludes_removed(self):
        ev = D._event_stats(self.db)
        crcs = {r["crc"] for r in D._garbage_table(self.db, ev)}
        self.assertEqual(crcs, {"quar", "paus"})

    def test_longevity_only_in_service(self):
        ev = D._event_stats(self.db)
        longevity, dropouts = D._degradation(self.db, ev)
        self.assertEqual([r["crc"] for r in longevity], ["ok"])
        self.assertEqual({r["crc"] for r in dropouts}, {"quar"})   # удалённая исключена

    def test_graveyard(self):
        ev = D._event_stats(self.db)
        g = D._graveyard(self.db, ev, 30)
        self.assertEqual(len(g), 1)
        r = g[0]
        self.assertEqual(r["crc"], "gone")
        self.assertEqual(r["peak"], 90)
        self.assertEqual(r["last"], 0)
        self.assertEqual(r["spark"], [60, 90, 40, 0])
        self.assertEqual(r["cause"], "quarantine")
        self.assertEqual(r["garbage_count"], 1)
        self.assertAlmostEqual(r["lifespan"], 12 * self.d, delta=5)
        self.assertAlmostEqual(r["purge_in"], 27 * self.d, delta=5)

    def test_downsample(self):
        self.assertEqual(D._downsample([1, 2, 3], 5), [1, 2, 3])
        out = D._downsample(list(range(100)), 30)
        self.assertEqual(len(out), 30)
        self.assertEqual((out[0], out[-1]), (0, 99))

    def test_user_and_tester_traffic_are_separate(self):
        from types import SimpleNamespace
        x = self.st._db.execute
        x("INSERT INTO traffic (ts,crc,up,down,conns,is_tester) VALUES (?,?,?,?,?,?)",
          (self.now, "ok", 100, 200, 1, 0))
        x("INSERT INTO traffic (ts,crc,up,down,conns,is_tester) VALUES (?,?,?,?,?,?)",
          (self.now + 1, "ok", 30, 40, 1, 1))
        self.st._db.commit()
        data = D._collect(SimpleNamespace(storage=_Cfg(self.db)))
        self.assertEqual(data["traffic_user_totals"], {"up": 100, "down": 200, "total": 300})
        self.assertEqual(data["traffic_tester_totals"], {"up": 30, "down": 40, "total": 70})
        self.assertEqual(data["traffic_range"]["start"], self.now)
        self.assertEqual(data["traffic_tester_range"]["start"], self.now + 1)


if __name__ == "__main__":
    unittest.main()
