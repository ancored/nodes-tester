"""Тест google_country: страна по заголовку X-Country сервера вариаций Chrome, срок 24 ч,
ячейка в своде результатов."""

import json
import unittest

from dashboard import data as D
from nodes_tester.config import parse_config
from nodes_tester.tests import TestContext
from nodes_tester.tests.google_country import GoogleCountryTest


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


if __name__ == "__main__":
    unittest.main()
