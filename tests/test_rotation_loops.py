"""Таймер ротации не срабатывает раз за разом: после любой оценки группы срок ротации в
будущем — при 0–3 нодах в группе, без кандидатов, при аварии, сбое API, пропавшей активной и
недобранном min_dwell."""

import itertools
import threading
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


class RotationSpreadTest(unittest.TestCase):
    """Плановые ротации разных групп не ближе rotation.spread: вторая группа ждёт, срок не
    переносится на целый интервал."""

    def test_second_group_waits(self):
        r = make_runner(temp_dir())
        try:
            groups = {"eu": NODES[:2], "us": [OTHER, "DEMO-vless-us-out [bbbb0002]"]}
            sw, board = r.switcher, r.board
            sw.api = r.api
            board.set_groups({g: set(m) for g, m in groups.items()})
            board.rows = {m: {**_row(m), "region": g} for g, ms in groups.items() for m in ms}
            r.api.all_proxies = lambda: {f"{g}-auto-out": {"type": "Selector", "all": list(m)}
                                         for g, m in groups.items()}
            now = time.time()
            sw.state = {g: {"active": m[0], "rotate_deadline": now - 1, "last_switch": now - 7200}
                        for g, m in groups.items()}
            for g, m in groups.items():
                board.set_reserve(g, m[1:])

            sw.rotate_due()
            moved = [g for g, m in groups.items() if sw.state[g]["active"] != m[0]]
            self.assertEqual(len(moved), 1)
            waiting = "us" if moved == ["eu"] else "eu"
            self.assertFalse(sw.rotation_due(waiting))
            self.assertAlmostEqual(sw.rotate_deadline(waiting), now + sw.cfg.rotation.spread, delta=5)
            self.assertLess(sw.state[waiting]["rotate_deadline"], now)   # срок не перенесён

            sw._rotated[moved[0]] -= sw.cfg.rotation.spread               # разнос выдержан
            sw.rotate_due()
            self.assertNotEqual(sw.state[waiting]["active"], groups[waiting][0])
        finally:
            r.storage.close()


class EmergencyCloseTest(unittest.TestCase):
    """Аварийная замена закрывает соединения упавшей ноды; плановая ротация — нет."""

    def test_close_on_emergency_only(self):
        r = make_runner(temp_dir())
        try:
            closed = []
            r.collector.open_user_conns = lambda crc: [f"{crc}-1", f"{crc}-2"]
            r.api.close_connection = closed.append
            r.reserve.wake = lambda: None
            for reason in ("rotation", "quality", "emergency"):
                r._on_activation("eu", reason, NODES[1], NODES[0])
                for t in [t for t in threading.enumerate() if t.name == "close-conns"]:
                    t.join(5)
                self.assertEqual(closed, ["aaaa0001-1", "aaaa0001-2"] if reason == "emergency" else [],
                                 reason)
        finally:
            r.storage.close()


if __name__ == "__main__":
    unittest.main()
