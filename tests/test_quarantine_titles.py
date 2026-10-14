"""Имена нод из подписок, карантин слабых нод (selection.quarantine.low_score) и сроки
ротации групп."""

import time
import unittest

from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
U = "DEMO-vless-us-out [bbbb0001]"


def _row(node, region, score=70.0, samples=5, active=0):
    return {"node": node, "region": region, "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": active, "samples": samples, "heavy_ok": "", "heavy_ts": 0}


class NodeTitlesTest(unittest.TestCase):
    """Имя ноды в подписке: из raw ветви по ключу, обновляется с raw, у ушедших остаётся."""

    def setUp(self):
        import json
        import os
        from naming import node_key
        self.tmp = temp_dir()
        self.r = make_runner(self.tmp)
        self.ob = {"type": "vless", "server": "192.0.2.1", "server_port": 443, "uuid": "u1"}
        self.key = node_key(self.ob)
        self.nodes_file = self.r.cfg.nodes_file
        with open(self.nodes_file, "w", encoding="utf-8") as fh:
            json.dump({"outbounds": [{**self.ob, "tag": f"P-vless-out [{self.key}]"}]}, fh)
        self.raw = os.path.join(self.tmp, "raw.json")
        self._raw("🇳🇱 Amsterdam")

    def tearDown(self):
        self.r.storage.close()

    def _raw(self, title):
        import json
        import os
        import time as t
        with open(self.raw, "w", encoding="utf-8") as fh:
            json.dump({"nodes": [{"provider": "P", "title": title, "outbound": self.ob},
                                 {"provider": "Q", "title": "дубль", "outbound": self.ob}]}, fh)
        stamp = t.time() + len(title)            # mtime меняется даже в пределах секунды
        os.utime(self.raw, (stamp, stamp))

    def _title(self):
        return self.r.storage._db.execute("SELECT title FROM nodes WHERE crc=?", (self.key,)).fetchone()[0]

    def test_title_from_raw_and_refresh(self):
        st = self.r.storage
        st.maybe_load_nodes(self.nodes_file, self.raw)
        self.assertEqual(self._title(), "🇳🇱 Amsterdam")   # дубль доступа — первое имя
        self._raw("NL Amsterdam 2")
        st.maybe_load_nodes(self.nodes_file, self.raw)
        self.assertEqual(self._title(), "NL Amsterdam 2")

    def test_title_by_server_when_credentials_changed(self):
        ob, self.ob = self.ob, {**self.ob, "uuid": "u2"}  # опрос без сборки: новые учётные данные
        self._raw("NL new uuid")
        self.ob = ob
        self.r.storage.maybe_load_nodes(self.nodes_file, self.raw)
        self.assertEqual(self._title(), "NL new uuid")

    def test_title_kept_without_raw(self):
        import os
        st = self.r.storage
        st.maybe_load_nodes(self.nodes_file, self.raw)
        os.remove(self.raw)
        st.load_titles(self.raw)
        self.assertEqual(self._title(), "🇳🇱 Amsterdam")


class LowScoreQuarantineTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), selection={"quarantine": {"low_score": 40}})
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
        r.cfg.selection.quarantine.low_score = 0     # выключено
        r.board.rows[A]["active"] = 0
        self.assertFalse(r._low_score(A))

    def test_quarantine_and_release(self):
        r = self.r
        r._backoff_update("aaaa0001", gate=True, low=True)
        until, streak, reason = r._backoff["aaaa0001"]
        self.assertEqual(reason, "garbage")
        self.assertAlmostEqual(until - time.time(), r.cfg.selection.quarantine.duration, delta=360)
        self.assertTrue(r._backed_off("aaaa0001"))
        events = r.storage._db.execute(
            "SELECT event, reason FROM node_events WHERE crc='aaaa0001'").fetchall()
        self.assertEqual(events, [("garbage", "low-score")])
        r._backoff["aaaa0001"] = (int(time.time()) - 1, streak, "garbage")      # срок вышел
        self.assertFalse(r._backed_off("aaaa0001"))                             # проба
        r._backoff_update("aaaa0001", gate=True, low=False)                     # поднялась
        self.assertNotIn("aaaa0001", r.storage.load_backoff())


class RotationDeadlineTest(unittest.TestCase):
    """Срок ротации у каждой группы свой: interval ± jitter от её переключения."""

    def _sw(self, **rot):
        from nodes_tester.config import RotationConfig, SelectionConfig
        from nodes_tester.switcher import Switcher
        cfg = SelectionConfig(rotation=RotationConfig(**rot))
        storage = type("S", (), {"load_switch_state": lambda self: {},
                                 "set_active_crcs": lambda self, crcs: None})()
        return Switcher(cfg, None, _Board(), "nodes-tester", storage=storage)

    def test_groups_switched_together_get_different_deadlines(self):
        sw = self._sw()
        deadlines = {sw._next_deadline(1000.0) for _ in range(20)}
        self.assertGreater(len(deadlines), 1)
        for d in deadlines:
            self.assertLessEqual(abs(d - (1000.0 + 10800)), 900)

    def test_no_jitter(self):
        self.assertEqual(self._sw(jitter=0)._next_deadline(1000.0), 1000.0 + 10800)


class _Board:
    def set_active(self, group, node):
        pass


if __name__ == "__main__":
    unittest.main()
