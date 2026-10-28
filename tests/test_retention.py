"""P0: активно тестируемая нода не должна удаляться retention'ом при статичном
nodes.json. touch_seen обновляет присутствие; отсутствующая нода удаляется по сроку."""

import time
import unittest

from nodes_tester import storage as S
from tests.helpers import fresh_nodes_json, temp_dir


class _Cfg:
    def __init__(self, db):
        self.path = db
        self.retention = 30 * 86400
        self.retention_days = 30


def _node(crc):
    return {"tag": f"LUNA-vless|reality-nl-out [{crc}]", "type": "vless",
            "server": "1.2.3.4", "server_port": 443}


class RetentionTest(unittest.TestCase):
    def setUp(self):
        import os
        self.st = S.Storage(_Cfg(os.path.join(temp_dir(), "s.db")))

    def tearDown(self):
        self.st.close()

    def _age(self, crc, days):
        old = int(time.time()) - days * 86400
        self.st._db.execute("UPDATE nodes SET last_seen=? WHERE crc=?", (old, crc))
        self.st._db.commit()

    def _count(self, crc):
        return self.st._db.execute(
            "SELECT COUNT(*) FROM nodes WHERE crc=?", (crc,)).fetchone()[0]

    def test_touch_seen_saves_active_node(self):
        self.st.load_nodes(fresh_nodes_json(temp_dir(), [_node("aaaa")]))
        self._age("aaaa", 31)                 # как будто nodes.json не менялся 31 день
        self.st.touch_seen(["aaaa"])          # но нода присутствует в selector
        self.st.cleanup()
        self.assertEqual(self._count("aaaa"), 1)   # жива

    def test_absent_node_removed_after_retention(self):
        self.st.load_nodes(fresh_nodes_json(temp_dir(), [_node("bbbb")]))
        self._age("bbbb", 31)                 # старая и БЕЗ touch_seen (исчезла из selector)
        self.st.cleanup()
        self.assertEqual(self._count("bbbb"), 0)   # удалена

    def test_drops_add_up_and_expire(self):
        now = int(time.time()) // 600 * 600
        old = now - 31 * 86400
        self.st.add_drops({(now, "aaaa", "busy"): 2, (old, "aaaa", "idle"): 1})
        self.st.add_drops({(now, "aaaa", "busy"): 3})
        self.st.add_monitor_emergency("eu", "aaaa", "drops", 10, 5)
        self.st.cleanup()
        rows = self.st._db.execute("SELECT ts, crc, outcome, n FROM drops").fetchall()
        self.assertEqual(rows, [(now, "aaaa", "busy", 5)])
        self.assertEqual(self.st._db.execute("SELECT region, crc, rule, current, previous "
                                             "FROM monitor_emergencies").fetchall(),
                         [("eu", "aaaa", "drops", 10, 5)])


if __name__ == "__main__":
    unittest.main()
