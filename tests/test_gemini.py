"""Gemini-тест и обязательные тесты групп: страна по мнению Google, решающий отбор
кандидатов, failsafe группы, замена активной ноды, цели отдельной фазы."""

import time
import unittest
from unittest import mock

from nodes_tester.identity import parse_node
from nodes_tester.tests import TestContext, TestResult
from nodes_tester.tests.gemini import GeminiTest, google_country
from tests.helpers import make_runner, temp_dir

PAGE = 'x,2,1,200,"{}"],y'


class _Resp:
    def __init__(self, status, cc=None):
        self.status_code = status
        self.text = PAGE.format(cc) if cc else "<html>no marker</html>"


class _Session:
    def __init__(self, answers):
        self.answers = list(answers)

    def get(self, url, headers=None, timeout=None):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(answers, **opts):
    ctx = TestContext(session=_Session(answers), node="n", default_timeout=5)
    return GeminiTest(opts).run(ctx)


class GeminiTestTest(unittest.TestCase):
    def test_country_parse(self):
        self.assertEqual(google_country(PAGE.format("NLD")), "NLD")
        self.assertIsNone(google_country("nothing"))

    def test_all_attempts_allowed(self):
        r = _run([_Resp(200, "NLD")] * 3)
        self.assertTrue(r.ok)
        self.assertEqual(r.metrics["country"], "NLD")

    def test_single_rus_attempt_fails(self):     # выход то по IPv4, то по IPv6 WARP
        r = _run([_Resp(200, "NLD"), _Resp(200, "RUS"), _Resp(200, "NLD")])
        self.assertFalse(r.ok)
        self.assertIn("RUS", r.error)
        self.assertEqual(r.metrics["countries"], ["NLD", "RUS", "NLD"])

    def test_403_and_network_error_and_no_marker(self):
        self.assertIn("403", _run([_Resp(403)], attempts=1).error)
        self.assertFalse(_run([ConnectionError("reset")], attempts=1).ok)
        self.assertEqual(_run([_Resp(200)], attempts=1).error, "страна не определена")

    def test_network_errors_with_allowed_country_inconclusive(self):
        r = _run([TimeoutError(), TimeoutError(), _Resp(200, "FRA")])
        self.assertFalse(r.ok)
        self.assertTrue(r.metrics["inconclusive"])
        self.assertIn("2/3", r.error)
        r = _run([TimeoutError(), _Resp(200, "RUS"), _Resp(200, "FRA")])
        self.assertNotIn("inconclusive", r.metrics)                  # запрещённая страна важнее
        r = _run([TimeoutError()] * 3)
        self.assertNotIn("inconclusive", r.metrics)                  # Google недоступен — провал
        r = _run([TimeoutError(), _Resp(403), _Resp(200, "FRA")])
        self.assertNotIn("inconclusive", r.metrics)

    def test_forbidden_list_configurable(self):
        self.assertTrue(_run([_Resp(200, "RUS")], attempts=1, forbidden_countries=["CHN"]).ok)


A = "DEMO-vless|reality-nl-out [aaaa0001]"
B = "DEMO-vless|reality-fi-out [aaaa0002]"
C = "DEMO-vless|reality-de-out [aaaa0003]"


class _Api:
    def __init__(self):
        sel = lambda tag, members: {"type": "selector", "all": [f"{tag}-failsafe"] + members,
                                    "now": f"{tag}-failsafe"}
        self.proxies = {A: {"type": "vless"}, B: {"type": "vless"}, C: {"type": "vless"},
                        "ai-auto-out": sel("ai-auto-out", [A, B, C]),
                        "ai-auto-out-failsafe": {"type": "urltest", "all": [A, B, C]},
                        "nodes-tester": {"type": "selector", "all": [A, B, C], "now": A}}
        self.selected = []

    def all_proxies(self):
        return self.proxies

    def select(self, group, node):
        self.selected.append((group, node))
        self.proxies[group]["now"] = node

    def list_group_members(self, group):
        return list(self.proxies[group]["all"])

    def current_selection(self, group):
        return self.proxies[group]["now"]


class RequiredGroupTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())
        self.addCleanup(self.r.storage.close)
        self.r.api = self.r.switcher.api = _Api()
        self.r.cfg.run.node_group_overrides = {"ai": {"required_tests": ["gemini"]}}
        self.r._region_cache = {}
        self.board, self.sw = self.r.board, self.r.switcher
        self.board.rows = {n: {"node": n, "region": "eu", "score": s, "id": parse_node(n).node_id,
                               "active": 0} for n, s in ((A, 90), (B, 80), (C, 70))}
        self.r._refresh_groups()

    def _pass(self, node, ok):
        self.board.set_required_result(node, "gemini", ok, "NLD" if ok else "RUS")

    def test_requirements_from_group_overrides(self):
        self.assertEqual(self.board.required("ai"), {"gemini": 6 * 3600})

    def test_only_passed_are_candidates_and_failsafe_when_none(self):
        self.assertEqual(self.board.candidates("ai"), [])
        self.sw.evaluate_all()
        self.assertIn(("ai-auto-out", "ai-auto-out-failsafe"), self.r.api.selected)
        self.assertIsNone(self.sw.active_node("ai"))
        self._pass(B, True)
        self._pass(A, False)
        self.assertEqual([c["node"] for c in self.board.candidates("ai")], [B])
        self.sw.evaluate_all()
        self.assertEqual(self.sw.active_node("ai"), B)
        self.assertEqual(self.r.api.proxies["ai-auto-out"]["now"], B)

    def test_active_failing_is_replaced(self):
        self._pass(B, True)
        self._pass(C, True)
        self.sw.evaluate_all()
        self.assertEqual(self.sw.active_node("ai"), B)
        self._pass(B, False)
        self.sw.evaluate_all()
        self.assertEqual(self.sw.active_node("ai"), C)

    def test_stale_result_needs_test(self):
        self._pass(A, True)
        self.assertFalse(self.board.needs_test(A, "gemini", 3600))
        self.board.rows[A]["gemini_ts"] = int(time.time()) - 7 * 3600    # старше 6 ч
        self.assertTrue(self.board.needs_test(A, "gemini", 6 * 3600))
        self.assertEqual(self.board.candidates("ai"), [])            # просроченный — не кандидат

    def test_targets_top_k_without_fresh_result_and_failed_drop_out(self):
        self.r.cfg.switching.rotation.top_k = 2
        node_by_raw = {n: ("eu", parse_node(n)) for n in (A, B, C)}
        first = [ident.raw for _t, _r, ident in self.r._required_targets(node_by_raw, set())]
        self.assertEqual(first, [A, B])
        self._pass(A, False)                                          # провал — выбыл из пула
        self._pass(B, True)
        nxt = [ident.raw for _t, _r, ident in
               self.r._required_targets(node_by_raw, {("gemini", A), ("gemini", B)})]
        self.assertEqual(nxt, [C])

    def test_inconclusive_keeps_previous_verdict(self):
        self.r._originals = {}
        self._pass(A, True)
        ts = self.board.rows[A]["gemini_ts"] = int(time.time()) - 60
        answer = TestResult("gemini", False, {"inconclusive": True, "countries": [None, "FRA"]},
                            error="сеть")
        with mock.patch.object(GeminiTest, "run", return_value=answer):
            self.r._required_round("gemini", [("eu", parse_node(A))], 1)
        self.assertEqual((self.board.rows[A]["gemini_ok"], self.board.rows[A]["gemini_ts"]), ("1", ts))
        answer.metrics.pop("inconclusive")
        with mock.patch.object(GeminiTest, "run", return_value=answer):
            self.r._required_round("gemini", [("eu", parse_node(A))], 1)
        self.assertEqual(self.board.rows[A]["gemini_ok"], "0")


if __name__ == "__main__":
    unittest.main()
