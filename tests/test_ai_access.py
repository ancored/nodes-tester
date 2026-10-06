"""Доступность OpenAI и Claude, строгий выходной IP группы (страна по базе, тип IP),
ячейки AI-тестов в своде результатов."""

import json
import os
import time
import unittest
from types import SimpleNamespace

from dashboard import data
from nodes_tester.config import ScoringConfig, StorageConfig, load_config
from nodes_tester.scoreboard import Scoreboard
from nodes_tester.storage import Storage
from nodes_tester.tests import TestContext
from nodes_tester.tests.ai_access import AnthropicTest, OpenAITest
from tests.helpers import make_config, temp_dir

BLOCKED_OPENAI = {"error": {"code": "unsupported_country_region_territory",
                            "message": "Country, region, or territory not supported",
                            "param": None, "type": "request_forbidden"}}


def _resp(status=200, body=None, location=""):
    def as_json():
        if body is None:
            raise ValueError("not json")
        return body
    return SimpleNamespace(status_code=status, json=as_json, headers={"Location": location})


class _Session:
    def __init__(self, answers):
        self.answers = list(answers)
        self.kwargs = []

    def get(self, url, **kwargs):
        self.kwargs.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(cls, answers, **opts):
    session = _Session(answers)
    ctx = TestContext(session=session, node="n", default_timeout=5)
    return cls(opts).run(ctx), session


class OpenAITestTest(unittest.TestCase):
    def test_allowed(self):
        r, _ = _run(OpenAITest, [_resp(body={"cookie_consent_required": True})] * 3)
        self.assertTrue(r.ok)
        self.assertEqual(r.metrics["verdicts"], ["allowed"] * 3)

    def test_unsupported_country_in_one_attempt_fails(self):
        r, _ = _run(OpenAITest, [_resp(body={"cookie_consent_required": False}),
                                 _resp(403, BLOCKED_OPENAI), _resp(body={"cookie_consent_required": True})])
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "сервис недоступен в регионе")

    def test_unrecognized_answer_fails(self):           # страница проверки Cloudflare
        r, _ = _run(OpenAITest, [_resp(403)], attempts=1)
        self.assertFalse(r.ok)
        self.assertIn("403", r.error)

    def test_network_errors_inconclusive(self):
        r, _ = _run(OpenAITest, [TimeoutError(), _resp(body={"cookie_consent_required": True})],
                    attempts=2)
        self.assertTrue(r.metrics.get("inconclusive"))
        r, _ = _run(OpenAITest, [TimeoutError()] * 2, attempts=2)
        self.assertNotIn("inconclusive", r.metrics)
        self.assertFalse(r.ok)


AUTH = {"type": "error", "error": {"type": "authentication_error",
                                   "message": "x-api-key header is required"}}
FORBIDDEN = {"error": {"type": "forbidden", "message": "Request not allowed"}}


class AnthropicTestTest(unittest.TestCase):
    def test_auth_error_means_allowed(self):
        r, _ = _run(AnthropicTest, [_resp(401, AUTH)], attempts=1)
        self.assertTrue(r.ok)

    def test_forbidden_region(self):
        r, _ = _run(AnthropicTest, [_resp(401, AUTH), _resp(403, FORBIDDEN)], attempts=2)
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "сервис недоступен в регионе")

    def test_challenge_fails(self):                   # страница проверки Cloudflare — не JSON
        r, _ = _run(AnthropicTest, [_resp(403)], attempts=1)
        self.assertFalse(r.ok)


def _row(node, score, ip, cc="NL"):
    return {"node": node, "region": "eu", "score": score, "id": node, "heavy_ok": "",
            "heavy_ts": 0, "exit_ip": ip, "exit_cc": cc, "required": {}}


IPS = {"1.0.0.1": {"country": "NL", "hosting": 1},                 # ДЦ
       "1.0.0.2": {"country": "NL"},                               # домашний
       "1.0.0.3": {"country": "NL", "mobile": 1, "proxy": 1},      # мобильный, но прокси
       "1.0.0.4": {"country": "DE"},                               # домашний, по базе DE
       "1.0.0.5": {"country": "NL", "mobile": 1}}                  # мобильный


class StrictExitIpTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(StorageConfig(db_file=os.path.join(temp_dir(), "s.db")))
        self.addCleanup(self.storage.close)
        self.sb = Scoreboard(self.storage, ScoringConfig())
        self.sb.rows = {"DC": _row("DC", 90, "1.0.0.1"), "HOME": _row("HOME", 50, "1.0.0.2"),
                        "PROXY": _row("PROXY", 80, "1.0.0.3"), "GEO": _row("GEO", 95, "1.0.0.4"),
                        "MOB": _row("MOB", 40, "1.0.0.5"), "NOINFO": _row("NOINFO", 85, "9.9.9.9")}
        self.sb.set_ip_info(IPS)

    def _cands(self):
        return [c["node"] for c in self.sb.candidates("eu")]

    def test_not_strict_group_unchanged(self):
        self.assertEqual(self._cands(), ["GEO", "DC", "NOINFO", "PROXY", "HOME", "MOB"])

    def test_strict_prefers_residential_and_mobile_drops_geo_mismatch(self):
        self.sb.set_strict({"eu"})
        self.assertEqual(self._cands(), ["HOME", "MOB"])
        self.assertEqual(self.sb.probe_pool("eu"), ["HOME", "MOB", "DC", "NOINFO", "PROXY"])

    def test_strict_falls_back_to_hosting_proxy_and_unknown(self):
        self.sb.set_strict({"eu"})
        del self.sb.rows["HOME"], self.sb.rows["MOB"]
        self.assertEqual(self._cands(), ["DC", "NOINFO", "PROXY"])

    def test_required_results_and_exit_ip_persist(self):
        self.sb.set_required_result("DC", "openai", True)
        self.sb.write()
        row = self.storage.load_scores()["DC"]
        self.assertEqual(row["required"]["openai"]["ok"], "1")
        self.assertEqual((row["exit_ip"], row["exit_cc"]), ("1.0.0.1", "NL"))
        self.assertEqual(self.storage.load_ip_info(), {})

    def test_record_takes_exit_ip_from_connectivity(self):
        from nodes_tester.identity import parse_node
        ident = parse_node("DEMO-vless|reality-nl-out [aaaa0001]")
        self.sb.record(ident, "eu", {"connectivity": {"ok": True, "exit_ip": "2.2.2.2", "country": "de"}})
        self.assertEqual((self.sb.rows[ident.raw]["exit_ip"], self.sb.rows[ident.raw]["exit_cc"]),
                         ("2.2.2.2", "DE"))
        self.sb.record(ident, "eu", {"connectivity": {"ok": False}})
        self.assertEqual(self.sb.rows[ident.raw]["exit_ip"], "2.2.2.2")     # прежний выход


class ConfigTest(unittest.TestCase):
    def test_required_tests_and_strict_flag(self):
        tmp = temp_dir()
        path = make_config(tmp)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw.setdefault("run", {})["node_groups_specifics"] = [
            {"group": "ai", "default_overrides": {"required_tests": ["gemini", "openai", "anthropic"],
                                                  "strict_exit_ip": True}}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        cfg = load_config(path)
        params = cfg.run.for_node_groups(cfg.run.default, ["ai"])
        self.assertEqual(params.required_tests, ["gemini", "openai", "anthropic"])
        self.assertTrue(params.strict_exit_ip)
        self.assertFalse(cfg.run.default.strict_exit_ip)


class ResultCellsTest(unittest.TestCase):
    def test_ai_cells(self):
        tmp = temp_dir()
        db = os.path.join(tmp, "s.db")
        st = Storage(StorageConfig(db_file=db))
        try:
            st.add_results({"id": "abcd1234", "round": 1, "tests": {
                "connectivity": {"ok": True, "exit_ip": "1.1.1.1", "country": "NL"}}})
            st.add_results({"id": "abcd1234", "round": 1, "tests": {
                "gemini": {"ok": False, "countries": ["NLD", "RUS", "NLD"], "country": "NLD",
                           "error": "Google видит страну RUS"},
                "openai": {"ok": True, "verdicts": ["allowed"]},
                "anthropic": {"ok": False, "inconclusive": True, "error": "сеть: 1/3"}}})
        finally:
            st.close()
        res = data._results(db)
        self.assertEqual(res["tests"][-3:], ["gemini", "openai", "anthropic"])
        cells = res["rows"][0]["cells"]
        self.assertEqual((cells["gemini"]["ok"], cells["gemini"]["cc"], cells["gemini"]["ai"]), (0, "RU", 1))
        self.assertEqual((cells["openai"]["ok"], cells["openai"]["v"]), (1, "Доступен"))
        self.assertEqual((cells["anthropic"]["unk"], cells["anthropic"]["v"]), (1, "Не определено"))


if __name__ == "__main__":
    unittest.main()
