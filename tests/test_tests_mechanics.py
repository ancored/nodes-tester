"""Качество измерений: классификация HTTP в download, кворум reachability,
требование exit_ip в connectivity, нейтральность http_error в скоринге."""

import unittest

import requests

from nodes_tester.tests import TestContext
from nodes_tester.tests.download import DownloadTest
from nodes_tester.tests.reachability import ReachabilityTest
from nodes_tester.tests.connectivity import ConnectivityTest
from nodes_tester import scoring


# --- Фейки сессии/ответа для стриминга (download) -------------------------

class _StreamResp:
    def __init__(self, status=200, chunks=(), content_length=None, http_status=None):
        self.status_code = status
        self._chunks = list(chunks)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._http_status = http_status          # если задан → raise_for_status бросит HTTPError

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self._http_status is not None:
            err = requests.HTTPError(str(self._http_status))
            err.response = type("R", (), {"status_code": self._http_status})()
            raise err

    def iter_content(self, n):
        for c in self._chunks:
            yield c

    def close(self):
        pass


class _Session:
    def __init__(self, resp=None, raise_exc=None, by_url=None):
        self._resp = resp
        self._raise = raise_exc
        self._by_url = by_url or {}

    def get(self, url, *a, **k):
        if self._raise is not None:
            raise self._raise
        if self._by_url:
            return self._by_url[url]
        return self._resp


def _ctx(session, region="eu"):
    return TestContext(session=session, node="x", default_timeout=5, region=region)


class DownloadClassifyTest(unittest.TestCase):
    def _run(self, resp=None, raise_exc=None, **opts):
        t = DownloadTest(opts)
        return t.run(_ctx(_Session(resp=resp, raise_exc=raise_exc))).to_dict()

    def test_404_is_http_error_not_ok(self):
        r = self._run(_StreamResp(http_status=404))
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("http_error"))
        self.assertEqual(r.get("http_status"), 404)

    def test_429_is_limited_ok(self):
        r = self._run(_StreamResp(http_status=429))
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("limited"))

    def test_503_is_limited_ok(self):
        r = self._run(_StreamResp(http_status=503))
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("limited"))

    def test_transport_break_is_fail(self):
        r = self._run(raise_exc=requests.ConnectionError("reset"))
        self.assertFalse(r["ok"])
        self.assertNotIn("http_error", r)

    def test_full_download_ok(self):
        chunks = [b"x" * 1000] * 10                      # 10 КБ, Content-Length совпал
        r = self._run(_StreamResp(chunks=chunks, content_length=10000))
        self.assertTrue(r["ok"])


class ReachabilityQuorumTest(unittest.TestCase):
    def _run(self, status_map, **opts):
        by_url = {u: _StreamResp(status=s) for u, s in status_map.items()}
        t = ReachabilityTest({"urls": list(status_map), **opts})
        return t.run(_ctx(_Session(by_url=by_url))).to_dict()

    def test_4xx_not_counted_reached(self):
        r = self._run({"a": 200, "b": 403, "c": 404})
        self.assertEqual(r["reached"], 1)               # только 200

    def test_quorum_not_met_is_fail(self):
        r = self._run({"a": 200, "b": 500, "c": 500}, min_reached=2)
        self.assertFalse(r["ok"])                        # достигнута 1 < кворум 2

    def test_quorum_met_ok(self):
        r = self._run({"a": 200, "b": 301, "c": 500}, min_reached=2)
        self.assertTrue(r["ok"])                         # 200 + 301 = 2


class ConnectivityExitIpTest(unittest.TestCase):
    def _run(self, body):
        resp = _StreamResp(status=200)
        resp.text = body
        return ConnectivityTest({"service": "cloudflare"}).run(
            _ctx(_Session(resp=resp))).to_dict()

    def test_requires_exit_ip(self):
        r = self._run("ip=1.2.3.4\nloc=NL\n")
        self.assertTrue(r["ok"])
        self.assertEqual(r["exit_ip"], "1.2.3.4")

    def test_no_exit_ip_is_fail(self):
        r = self._run("garbage without ip\n")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("transport_ok"))           # транспорт дошёл, выход не подтверждён


class ScoringNeutralHttpErrorTest(unittest.TestCase):
    def setUp(self):
        from nodes_tester.config import _DEFAULT_THRESHOLDS
        self.th = dict(_DEFAULT_THRESHOLDS)

    def test_http_error_does_not_zero_throughput(self):
        comps = scoring.components({"download": {"ok": False, "http_error": True}}, self.th)
        self.assertNotIn("throughput", comps)            # не штрафуем — сервер ответил

    def test_transport_break_zeroes_throughput(self):
        comps = scoring.components({"download": {"ok": False}}, self.th)
        self.assertEqual(comps.get("throughput"), 0.0)   # реальный провал транспорта


if __name__ == "__main__":
    unittest.main()
