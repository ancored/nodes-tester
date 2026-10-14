"""Таймер ротации не срабатывает раз за разом: после любой оценки группы срок ротации в
будущем — при 0–3 нодах в группе, без кандидатов, при аварии, сбое API, пропавшей активной и
недобранном min_dwell."""

import itertools
import time
import unittest

from nodes_tester.api import ApiError
from tests.helpers import make_runner, temp_dir

NODES = [f"DEMO-vless-nl-out [aaaa000{i}]" for i in range(1, 4)]
OTHER = "DEMO-vless-us-out [bbbb0001]"


def _row(node, score=70.0):
    return {"node": node, "region": "eu", "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": 0, "samples": 5, "heavy_ok": "", "heavy_ts": 0}


class RotationLoopTest(unittest.TestCase):
    SCENARIOS = ("healthy", "all_zero", "active_restricted", "api_down",
                 "active_gone", "dwell", "emergency", "active_zero")

    def _check(self, n, scenario):
        r = make_runner(temp_dir())
        try:
            members = NODES[:n]
            sw, board = r.switcher, r.board
            sw.api = r.api                     # у переключателя своя ссылка на API
            board.set_groups({"eu": set(members) or {OTHER}})
            board.rows = {m: _row(m) for m in members}
            if scenario == "api_down":
                def down():
                    raise ApiError("connection refused")
                r.api.all_proxies = down
            else:
                r.api.all_proxies = lambda: {"eu-auto-out": {"type": "Selector", "all": list(members)}}
            now = time.time()
            active = members[0] if members else OTHER
            st = {"active": active, "rotate_deadline": now - 1, "last_switch": now - 7200}
            if scenario == "dwell":
                st.update(last_switch=now - 10, rotate_deadline=now - 1)
            if scenario == "active_gone":
                board.rows.pop(active, None)
            if scenario == "all_zero":
                for row in board.rows.values():
                    row["score"] = 0.0
            if scenario == "active_zero" and active in board.rows:
                board.rows[active]["score"] = 0.0
            if scenario == "active_restricted":
                board.set_restricted({active[-9:-1]})
            sw.state = {"eu": st}

            if scenario == "emergency":
                sw.evaluate_region("eu", emergency=True)
            else:
                sw.evaluate_all()
            target = sw.next_rotate_deadline()
            msg = f"нод {n}, сценарий {scenario}: {sw.state['eu']}"
            if scenario == "emergency" and n >= 2:
                self.assertNotEqual(sw.state["eu"]["active"], active, msg)   # замена была
            if sw.state["eu"].get("active"):
                self.assertIsNotNone(target, msg)
                self.assertGreater(target, time.time() + 30, msg)
                self.assertFalse(sw.rotation_due("eu"), msg)
        finally:
            r.storage.close()

    def test_all_sizes_and_scenarios(self):
        for n, scenario in itertools.product(range(4), self.SCENARIOS):
            with self.subTest(nodes=n, scenario=scenario):
                self._check(n, scenario)


if __name__ == "__main__":
    unittest.main()
