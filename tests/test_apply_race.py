"""Прогон и применение конфига конвейером: прерывание прогона, пропуск нод, исчезнувших
из селектора; перенос срока ротации, когда ротировать не на что."""

import time
import unittest
from unittest.mock import patch

from nodes_tester.api import ApiError
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
        r._tests_cache = {}
        r.api.all_proxies = lambda: {"eu-auto-out": {"all": [A, B]},
                                     "us-auto-out": {"all": [U]}}
        r.board.rows = {A: _row(A, "eu"), B: _row(B, "eu"), U: _row(U, "us")}
        self.tested, self.scored, self.evaluated = [], [], []

    def tearDown(self):
        self.r.storage.close()

    def _pass(self, on_test=None):
        r = self.r

        def fake_test(region, ident, pass_no, tests, params):
            self.tested.append(ident.raw)
            if on_test:
                return on_test(ident.raw)
            return {"round": pass_no, "id": ident.node_id, "tests": {}}

        with patch.object(r, "_test_node", side_effect=fake_test), \
                patch.object(r, "_respect_host_gap"), \
                patch.object(r, "_score_and_maybe_switch",
                             side_effect=lambda reg, i, rec: self.scored.append(i.raw) or True), \
\
                patch.object(r.switcher, "evaluate_all",
                             side_effect=lambda groups=None: self.evaluated.append(groups)):
            return r._run_pass(1)

    def test_apply_mid_pass_aborts_without_scoring(self):
        r = self.r

        def apply_starts(raw):
            r._apply_start()                   # конвейер начал применять во время замера
            return {"tests": {}}

        self._pass(apply_starts)
        self.assertEqual(len(self.tested), 1)  # остальные ноды не тестировались
        self.assertEqual(self.scored, [])      # замер в окне применения отброшен
        self.assertEqual(self.evaluated, [])   # переключение не оценивалось
        self.assertTrue(r._pass_aborted)
        self.assertEqual(set(r.board.rows), {A, B, U})   # рейтинг не потерян

    def test_aborted_scope_is_kept(self):
        r = self.r
        r._scoped_next = True
        r._apply_start()
        self._pass()
        self.assertTrue(r._scoped_next)
        self.assertEqual(self.tested, [])

    def test_node_gone_from_selector_is_skipped(self):
        self._pass(lambda raw: None if raw == A else {"tests": {}})
        self.assertNotIn(A, self.scored)
        self.assertEqual(sorted(self.scored), sorted([B, U]))
        self.assertIn(A, self.r.board.rows)
        self.assertFalse(self.r._pass_aborted)

    def test_select_not_found_returns_none(self):
        r = self.r

        def select(group, node):
            raise ApiError(f"API SelectOutbound: grpc-status 5 outbound not found in selector: {node}")

        r.api.select = select
        r._originals = {}
        from nodes_tester.identity import parse_node
        params = r._node_params(parse_node(A))
        self.assertIsNone(r._test_node("eu", parse_node(A), 1, [], params))


class RotationSingleCandidateTest(unittest.TestCase):
    def setUp(self):
        self.r = r = make_runner(temp_dir(), tags=[U])
        r.board.rows = {U: _row(U, "us")}
        now = time.time()
        r.switcher.state = {"us": {"active": U, "rotate_deadline": now - 1,
                                   "last_switch": now - 7200}}

    def tearDown(self):
        self.r.storage.close()

    def test_deadline_moves_when_nothing_to_rotate(self):
        r = self.r
        self.assertTrue(r.switcher.rotation_due("us"))
        r.switcher.evaluate_all({"us"})
        self.assertFalse(r.switcher.rotation_due("us"))   # rotation_bound не крутит прогоны
        self.assertGreater(r.switcher.state["us"]["rotate_deadline"], time.time())
        self.assertEqual(r.switcher.state["us"]["active"], U)


if __name__ == "__main__":
    unittest.main()
