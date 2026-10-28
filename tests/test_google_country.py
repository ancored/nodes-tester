"""Тест google_country: страна по заголовку X-Country сервера вариаций Chrome, срок 24 ч,
ячейка в своде результатов; проверка группы google_country (prefer/require)."""

import json
import os
import time
import unittest

from dashboard import data as D
from nodes_tester.config import RatingConfig, StorageConfig, parse_config
from nodes_tester.scoreboard import Scoreboard
from nodes_tester.storage import Storage
from nodes_tester.tests import TestContext
from nodes_tester.tests.google_country import GoogleCountryTest
from tests.helpers import temp_dir


class _Resp:
    def __init__(self, status, country=None):
        self.status_code = status
        self.headers = {"X-Country": country} if country else {}


class _Session:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def head(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((url, allow_redirects))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _run(answer, **opts):
    session = _Session(answer)
    ctx = TestContext(session=session, node="n", default_timeout=5)
    return GoogleCountryTest(opts).run(ctx), session


class GoogleCountryTestTest(unittest.TestCase):
    def test_country_from_header(self):
        r, session = _run(_Resp(200, "ru"))
        self.assertTrue(r.ok)
        self.assertEqual(r.metrics, {"country": "RU"})
        self.assertEqual(session.calls, [(GoogleCountryTest.default_url, False)])

    def test_failures(self):
        self.assertEqual(_run(_Resp(200))[0].error, "нет заголовка X-Country")
        self.assertEqual(_run(_Resp(503, "pl"))[0].error, "HTTP 503")
        r, _ = _run(ConnectionError("reset"))
        self.assertFalse(r.ok)
        self.assertIn("ConnectionError", r.error)

    def test_daily_by_default(self):
        tests = parse_config({}).tests
        self.assertEqual(tests["google_country"]["ttl"], 86400)
        self.assertEqual(tests["latency"]["ttl"], 3 * 3600)
        self.assertEqual(parse_config({"tests": {"google_country": {"ttl": "12h"}}}).tests["google_country"]["ttl"],
                         12 * 3600)

    def test_cell(self):
        ru = D._cell("google_country", 1, json.dumps({"country": "RU"}))
        self.assertEqual((ru["v"], ru["ok"], ru["cc"]), ("RU", 0, "RU"))
        gb = D._cell("google_country", 1, json.dumps({"country": "GB"}))
        self.assertEqual((gb["ok"], gb["cc"]), (1, "GB"))
        failed = D._cell("google_country", 0, "{}", "HTTP 503")
        self.assertEqual((failed["unk"], failed["title"]), (1, "HTTP 503"))


class GoogleCountryCheckTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(StorageConfig(path=os.path.join(temp_dir(), "s.db")))
        self.sb = Scoreboard(self.storage, RatingConfig())
        self.sb.rows = {n: {"node": n, "id": n, "score": 80, "provider": "P", "protocol": "vless"} for n in "ABC"}
        self.sb.set_groups({"ai": set(self.sb.rows)})

    def tearDown(self):
        self.storage.close()

    def _policy(self, **params):
        cfg = parse_config({"groups": [{"tag": "ai", "prefer": {"google_country": params}}]})
        self.sb.set_policy(cfg.groups, {})

    def _verdicts(self):
        return {n: self.sb.check_report(n, "ai")[0]["verdict"] for n in "ABC"}

    def test_prefer_country_outside_list(self):
        self._policy()
        self.sb.set_required_result("A", "google_country", True, "RU")
        self.sb.set_required_result("B", "google_country", True, "NL")
        self.assertEqual(self._verdicts(), {"A": "0", "B": "1", "C": ""})
        self.assertEqual((self.sb.tier("A", "ai"), self.sb.tier("B", "ai")), (1, 0))

    def test_own_countries_and_expiry(self):
        self._policy(exclude_countries=["NL"])
        self.sb.set_required_result("A", "google_country", True, "RU")
        self.sb.set_required_result("B", "google_country", True, "NL")
        verdicts = self._verdicts()
        self.assertEqual((verdicts["A"], verdicts["B"]), ("1", "0"))
        self.sb.rows["A"]["required"]["google_country"]["ts"] = int(time.time()) - 3 * 86400   # старше 48 ч
        self.assertEqual(self._verdicts()["A"], "")

    def test_prefer_order_is_importance(self):
        """Первая проверка prefer важнее всех следующих: не RU у Google с ДЦ раньше RU с домашним IP."""
        cfg = parse_config({"groups": [{"tag": "ai", "prefer": {"google_country": {},
                                                                "ip_type": {"kinds": ["residential"]}}}]})
        self.sb.set_policy(cfg.groups, {})
        self.sb.set_ip_info({"1.1.1.1": {"hosting": True, "country": "PL", "ts": int(time.time())},
                             "2.2.2.2": {"country": "PL", "ts": int(time.time())}})
        for node, ip, google in (("A", "1.1.1.1", "PL"), ("B", "2.2.2.2", "RU"), ("C", "1.1.1.1", "RU")):
            self.sb.rows[node].update(exit_ip=ip, exit_cc="PL")
            self.sb.set_required_result(node, "google_country", True, google)
        self.assertEqual([self.sb.tier(n, "ai") for n in "ABC"], [1, 2, 3])
        self.sb.tier_floor = 90                             # слабая нода — после всех ярусов
        self.assertEqual(self.sb.tier("A", "ai"), 1 + 4)
        self.assertEqual(self.sb.tier("unknown", "ai"), 3)

    def test_restore_from_latest_results(self):
        self._policy()
        now = int(time.time())
        self.sb.set_required_result("B", "google_country", True, "NL")
        self.sb.restore_google_country({
            "A": {"google_country": (now - 60, {"ok": True, "country": "US"})},
            "B": {"google_country": (now - 86400, {"ok": True, "country": "RU"})},   # старее известного
            "C": {"google_country": (now, {"ok": False, "error": "HTTP 503"})}})
        self.assertEqual(self._verdicts(), {"A": "1", "B": "1", "C": ""})


if __name__ == "__main__":
    unittest.main()
