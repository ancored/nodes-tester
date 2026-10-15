"""Замер и применение конфига конвейером: замер на перезапуске sing-box не засчитывается,
ноды, исчезнувшие из селектора, пропускаются; перенос срока ротации, когда ротировать не на что."""

import time
import unittest
from unittest.mock import patch

from nodes_tester.api import ApiError
from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
U = "DEMO-vless-us-out [bbbb0001]"


def _row(node, region, score=70.0):
    return {"node": node, "region": region, "score": score, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": 0, "samples": 5, "heavy_ok": "", "heavy_ts": 0}


class ApplyRaceTest(unittest.TestCase):
    def setUp(self):
        self.r = r = make_runner(temp_dir(), tags=[A, B, U])
        r.board.rows = {A: _row(A, "eu"), B: _row(B, "eu"), U: _row(U, "us")}
        self.scored = []

    def tearDown(self):
        self.r.storage.close()

    def _measure(self, record):
        r = self.r
        with patch.object(r, "_test_node", return_value=record),                 patch.object(r, "_respect_host_gap"),                 patch.object(r, "_score_and_maybe_switch",
                             side_effect=lambda reg, i, tests: self.scored.append(i.raw) or True):
            return r._measure(frozenset({"eu"}), parse_node(A), ["connectivity"])

    def test_apply_mid_measure_not_scored(self):
        r = self.r

        def apply_starts(*_args):
            r._apply_start()                   # конвейер начал применять во время замера
            return {"id": "aaaa0001", "tests": {"_select": {"ok": True}}}

        with patch.object(r, "_test_node", side_effect=apply_starts),                 patch.object(r, "_respect_host_gap"):
            self.assertIsNone(r._measure(frozenset(), parse_node(A), ["connectivity"]))
        self.assertEqual(r.storage.latest_results(["connectivity"]), {})
        self.assertEqual(set(r.board.rows), {A, B, U})   # рейтинг не потерян

    def test_select_fails_while_api_down_not_scored(self):
        r = self.r

        def api_down():
            raise ApiError("Connection refused")

        r.api.ping = api_down
        self.assertIsNone(self._measure({"tests": {"_select": {"ok": False, "error": "refused"}}}))
        self.assertEqual(self.scored, [])
        self.assertTrue(r._apply_paused())     # пауза, пока ядро поднимается

    def test_failure_after_singbox_restart_not_scored_once(self):
        r = self.r
        r._box_pid = "100"
        failed = {"id": "aaaa0001", "tests": {"_select": {"ok": True}, "connectivity": {"ok": False}}}
        with patch.object(r.singbox, "pid", return_value="200"):
            self.assertIsNone(self._measure(failed))   # сверка при PID 100, провал при 200
            self.assertEqual(self.scored, [])
            self.assertFalse(r._box_interrupted(failed))   # PID тот же — провал ноды

    def test_failure_with_box_alive_is_scored(self):
        failed = {"id": "aaaa0001", "tests": {"_select": {"ok": True}, "connectivity": {"ok": False}}}
        self.assertTrue(self._measure(failed))
        self.assertEqual(self.scored, [A])

    def test_node_gone_from_selector_is_skipped(self):
        self.assertIsNone(self._measure(None))
        self.assertEqual(self.scored, [])
        self.assertIn(A, self.r.board.rows)

    def test_select_not_found_returns_none(self):
        r = self.r

        def select(group, node):
            raise ApiError(f"API SelectOutbound: grpc-status 5 outbound not found in selector: {node}")

        r.api.select = select
        r._originals = {}
        self.assertIsNone(r._test_node(frozenset({"eu"}), parse_node(A), ["connectivity"]))


class ApplyEndTest(unittest.TestCase):
    """Конвейер применил конфиг: выбор селекторов и состав нод — сразу, замеры — после паузы."""

    def setUp(self):
        self.r = make_runner(temp_dir(), tags=[A])
        self.refreshes = []
        self.r._refresh_nodes = lambda: self.refreshes.append(self.r._apply_in_progress)

    def tearDown(self):
        self.r.storage.close()

    def _wait(self, sleeps):
        """_wait_apply_window: каждый sleep — следующий шаг из sleeps, потом конец паузы."""
        def step(_seconds):
            if sleeps:
                sleeps.pop(0)()
            else:
                self.r._apply_pause_until = 0.0
        with patch("nodes_tester.runner.time.sleep", side_effect=step):
            self.r._wait_apply_window()

    def test_apply_end_reasserts_and_refreshes_in_pause(self):
        r = self.r
        with patch.object(r.monitor, "reassert") as reassert:
            r._apply_start()
            self._wait([lambda: self.assertEqual(self.refreshes, []), r._apply_end])
        reassert.assert_called_once()
        self.assertEqual(self.refreshes, [False])          # один раз, уже после конвейера
        self.assertFalse(r._stale_nodes)

    def test_refresh_retried_while_api_down(self):
        r = self.r
        down = [ApiError("Connection refused")]

        def refresh():
            if down:
                raise down.pop()
            self.refreshes.append(r._apply_in_progress)
        r._refresh_nodes = refresh
        with patch.object(r.monitor, "reassert"):
            r._apply_end()
        self._wait([lambda: None])
        self.assertEqual(self.refreshes, [False])


class RotationSingleCandidateTest(unittest.TestCase):
    def setUp(self):
        self.r = r = make_runner(temp_dir(), tags=[U])
        r.board.rows = {U: _row(U, "us")}
        r.board.set_groups({"us": {U}})
        now = time.time()
        r.switcher.state = {"us": {"active": U, "rotate_deadline": now - 1,
                                   "last_switch": now - 7200}}

    def tearDown(self):
        self.r.storage.close()

    def test_deadline_moves_when_nothing_to_rotate(self):
        r = self.r
        self.assertTrue(r.switcher.rotation_due("us"))
        r.switcher.evaluate_all({"us"})
        self.assertFalse(r.switcher.rotation_due("us"))   # таймер ротации не срабатывает снова
        self.assertGreater(r.switcher.state["us"]["rotate_deadline"], time.time())
        self.assertEqual(r.switcher.state["us"]["active"], U)


if __name__ == "__main__":
    unittest.main()
