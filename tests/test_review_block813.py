"""Блок 8-13 review.md: region-aware switcher, возрастной кап истории живых нод,
транзакционный traffic flush (возврат дельт при ошибке записи)."""

import time
import unittest

from tests.helpers import make_runner, temp_dir


class RegionAwareChainTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())

    def tearDown(self):
        self.r.storage.close()

    def test_activation_does_not_touch_foreign_region(self):
        # Нода nX — фолбэк-член и eu-auto-out, и us-auto-out (пустой регион залит).
        proxies = {
            "nX": {"type": "vless", "all": []},
            "eu-auto-out": {"type": "selector", "all": ["eu-auto-out-failsafe", "nX"]},
            "us-auto-out": {"type": "selector", "all": ["us-auto-out-failsafe", "nX"]},
            "global-auto-out": {"type": "selector", "all": ["eu-auto-out", "us-auto-out"]},
        }
        skip = {"nodes-tester", "global-auto-out"}
        sels = [s for s, _ in self.r.switcher._chain_pairs("nX", proxies, skip, "eu")]
        self.assertIn("eu-auto-out", sels)
        self.assertNotIn("us-auto-out", sels)     # чужой регион не трогаем


class HistoryCapTest(unittest.TestCase):
    def test_old_raw_facts_capped_even_for_live_node(self):
        import json, os
        from nodes_tester import storage as S

        class Cfg:
            db_file = os.path.join(temp_dir(), "s.db")
            retention_days = 30
        st = S.Storage(Cfg())
        p = os.path.join(temp_dir(), "n.json")
        json.dump({"outbounds": [{"tag": "LUNA-vless|reality-nl-out [cc]", "type": "vless",
                                  "server": "1.2.3.4", "server_port": 443}]},
                  open(p, "w", encoding="utf-8"))
        st.load_nodes(p)                          # нода живая (last_seen=now)
        now = int(time.time())
        st._db.execute("INSERT INTO results (ts,pass_no,crc,test,ok) VALUES (?,?,?,?,?)",
                       (now - 40 * 86400, 1, "cc", "latency", 1))   # старая
        st._db.execute("INSERT INTO results (ts,pass_no,crc,test,ok) VALUES (?,?,?,?,?)",
                       (now, 2, "cc", "latency", 1))                # свежая
        st._db.commit()
        st.cleanup()
        n = st._db.execute("SELECT COUNT(*) FROM results WHERE crc='cc'").fetchone()[0]
        node_alive = st._db.execute("SELECT COUNT(*) FROM nodes WHERE crc='cc'").fetchone()[0]
        st.close()
        self.assertEqual(n, 1)                    # старая срезана, свежая осталась
        self.assertEqual(node_alive, 1)           # сама нода жива


class TrafficFlushTest(unittest.TestCase):
    def test_flush_restores_buffer_on_write_error(self):
        from nodes_tester.traffic import TrafficCollector

        class BadStorage:
            def add_traffic_batch(self, *a):
                raise RuntimeError("db locked")

        class Cfg:
            poll_interval = 5
            flush_interval = 60
        col = TrafficCollector(Cfg(), api=None, storage=BadStorage())
        col._by_node[("crc", 0)] = [10, 20, 1]
        with self.assertRaises(RuntimeError):
            col._flush()
        self.assertEqual(col._by_node[("crc", 0)], [10, 20, 1])   # дельты не потеряны


if __name__ == "__main__":
    unittest.main()
