"""Дашборд: свод результатов — последний результат каждого теста со своим временем."""

import time
import unittest

from dashboard import data as D


class ResultsTest(unittest.TestCase):
    def _run(self, rows):
        orig = D._query
        D._query = lambda db, sql: rows
        try:
            return D._results("nodb")
        finally:
            D._query = orig

    def _row(self, test, ts, _seq=0):
        return {"crc": "z", "test": test, "ok": 1, "metrics": "{}", "error": "",
                "ts": ts, "provider": "P", "protocol": "vless", "country": "nl"}

    def test_latest_result_of_each_test_with_its_time(self):
        now = int(time.time())
        old_latency = self._row("latency", now - 7200)
        old_latency["ok"] = 0
        rows = [old_latency, self._row("connectivity", now - 3600),
                self._row("latency", now)]                    # строки идут по времени
        node = self._run(rows)["rows"][0]
        self.assertEqual(node["cells"]["latency"]["ok"], 1)
        self.assertEqual(node["cells"]["latency"]["ts"], now)
        self.assertEqual(node["cells"]["connectivity"]["ts"], now - 3600)
        self.assertEqual(node["ts"], now)

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

    def test_connectivity_and_whitelist_are_marks(self):
        import json
        now = int(time.time())
        conn = self._row("connectivity", now, 7)
        conn["metrics"] = json.dumps({"country": "NL"})
        wl = self._row("whitelist", now + 300, 7)
        wl["metrics"] = json.dumps({"ips": ["203.0.113.5"], "ip_ok": True, "sni": "vk.com", "sni_ok": True})
        res = self._run([conn, wl])
        row = res["rows"][0]
        self.assertEqual((row["cells"]["connectivity"]["ok"], row["cells"]["connectivity"]["ai"]), (1, 1))
        self.assertNotIn("NL", row["cells"]["connectivity"]["v"])
        self.assertEqual(row["cells"]["whitelist"]["ok"], 1)
        self.assertIn("SNI vk.com: в списке", row["cells"]["whitelist"]["title"])
        self.assertEqual(res["tests"][-1], "whitelist")

    def test_country_from_previous_connection_is_marked(self):
        now = int(time.time())
        ok = self._row("connectivity", now - 1800, 6)
        failed = self._row("connectivity", now, 7)
        failed.update(ok=0, error="timeout")
        row = self._run([ok, failed])["rows"][0]
        self.assertEqual((row["country"], row["country_stale"], row["country_ts"]), ("nl", 1, now - 1800))
        fresh = self._run([self._row("connectivity", now, 7)])["rows"][0]
        self.assertNotIn("country_stale", fresh)

    def test_all_check_columns_even_without_results(self):
        res = self._run([self._row("connectivity", int(time.time()), 7)])
        self.assertEqual(res["tests"][-4:], ["gemini", "openai", "anthropic", "whitelist"])
        self.assertNotIn("whitelist", res["rows"][0]["cells"])     # ячейка — прочерк


class _Cfg:
    def __init__(self, db):
        self.path = db
        self.retention = 30 * 86400
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
        x("INSERT INTO results (ts,crc,test,ok,metrics) VALUES (?,?,?,?,?)",
          (self.now, "ok", "download", 1, '{"downloaded":10000000}'))
        x("INSERT INTO results (ts,crc,test,ok,metrics) VALUES (?,?,?,?,?)",
          (self.now, "ok", "heavy_download", 0, '{"downloaded":5000000}'))
        x("INSERT INTO results (ts,crc,test,ok,metrics) VALUES (?,?,?,?,?)",
          (self.now - 90000, "ok", "download", 1, '{"downloaded":10000000}'))
        self.st._db.commit()
        data = D._collect(SimpleNamespace(storage=_Cfg(self.db)))
        self.assertEqual(data["traffic_user_totals"], {"up": 100, "down": 200, "total": 300})
        self.assertEqual(data["traffic_tester_totals"], {"up": 30, "down": 40, "total": 70})
        self.assertEqual(data["traffic_test_download_24h"]["down"], 15000000)
        self.assertEqual(data["traffic_test_download_24h"]["tests"], 2)
        self.assertEqual(data["traffic_range"]["start"], self.now)
        self.assertEqual(data["traffic_tester_range"]["start"], self.now + 1)

    def test_removed_nodes_only_in_archive(self):
        from types import SimpleNamespace
        x = self.st._db.execute
        x("INSERT INTO nodes (crc, tag, provider, first_seen, title) VALUES ('new', 'new', 'P', ?, 'NL 1')",
          (self.now,))
        x("UPDATE nodes SET title = 'DE old' WHERE crc = 'gone'")
        for crc in ("ok", "gone", "stale"):
            x("INSERT INTO results (ts,crc,test,ok,metrics) VALUES (?,?,?,?,?)",
              (self.now, crc, "latency", 1, '{"ms": 50}'))
        self.st._db.commit()
        data = D._collect(SimpleNamespace(storage=_Cfg(self.db)))
        nodes = {n["crc"]: n for n in data["nodes"]}
        self.assertNotIn("gone", nodes)
        self.assertEqual(nodes["new"]["title"], "NL 1")   # собрана конвейером, тестер ещё не сверил
        self.assertIsNone(nodes["ok"]["title"])
        rows = {r["crc"]: r.get("removed") for r in data["results"]["rows"]}
        self.assertEqual(rows, {"ok": None, "gone": 1})    # ушедшая — для карточки, ключ без ноды — нет
        self.assertEqual([(g["crc"], g["title"]) for g in data["graveyard"]], [("gone", "DE old")])

    def test_client_nodes_listed_with_whitelist_only(self):
        import json
        import os
        from types import SimpleNamespace
        wh = os.path.join(os.path.dirname(self.db), "whnodes.json")
        with open(wh, "w", encoding="utf-8") as fh:
            json.dump({"outbounds": [{"type": "selector", "tag": "global-auto-out"},
                                     {"type": "vless", "tag": "WH-SNI-vless|reality-out [bbbb0002.00000002]",
                                      "server": "192.0.2.1"}]}, fh)
        self.st._db.execute("INSERT INTO results (ts,crc,test,ok,metrics) VALUES (?,?,?,?,?)",
                            (self.now, "bbbb0002.00000002", "whitelist", 1, '{"ip_ok": true, "ips": ["192.0.2.1"]}'))
        self.st._db.commit()
        data = D._collect(SimpleNamespace(storage=_Cfg(self.db), whnodes_file=wh))
        node = next(n for n in data["nodes"] if n["crc"] == "bbbb0002.00000002")
        self.assertEqual((node["provider"], node["protocol"], node["wh_only"], node["score"]),
                         ("WH-SNI", "vless|reality", 1, None))
        row = next(r for r in data["results"]["rows"] if r["crc"] == "bbbb0002.00000002")
        self.assertEqual((row["provider"], row["wh_only"], row["cells"]["whitelist"]["ok"]), ("WH-SNI", 1, 1))
        # Раньше нода была у роутера (в архиве): в клиентской ветви её результаты не прячутся.
        self.st._db.execute("INSERT INTO nodes (crc, tag, present) VALUES (?, ?, 0)",
                            ("bbbb0002.00000002", "WH-SNI-vless|reality-out [bbbb0002.00000002]"))
        self.st._db.commit()
        data = D._collect(SimpleNamespace(storage=_Cfg(self.db), whnodes_file=wh))
        row = next(r for r in data["results"]["rows"] if r["crc"] == "bbbb0002.00000002")
        self.assertEqual((row["wh_only"], row.get("removed")), (1, None))


if __name__ == "__main__":
    unittest.main()
