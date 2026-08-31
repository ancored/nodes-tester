"""P1: HTTP 5xx и битый JSON Clash API нормализуются в ClashApiError, а не
пробрасываются наружу голым HTTPError/ValueError, роняя процесс."""

import unittest

import requests

from nodes_tester.clash_api import ClashApiClient, ClashApiError
from nodes_tester.config import ClashApiConfig


class _Resp:
    def __init__(self, status=200, data=None, bad_json=False):
        self.status_code = status
        self._data = data if data is not None else {}
        self._bad = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._data


class ClashApiErrorTest(unittest.TestCase):
    def setUp(self):
        self.cl = ClashApiClient(ClashApiConfig())

    def test_http_500_becomes_clash_error(self):
        self.cl._get = lambda p: _Resp(500)
        with self.assertRaises(ClashApiError):
            self.cl.all_proxies()

    def test_bad_json_becomes_clash_error(self):
        self.cl._get = lambda p: _Resp(200, bad_json=True)
        with self.assertRaises(ClashApiError):
            self.cl.connections()

    def test_404_proxy_is_clash_error(self):
        self.cl._get = lambda p: _Resp(404)
        with self.assertRaises(ClashApiError):
            self.cl.get_proxy("missing")

    def test_ok_returns_json(self):
        self.cl._get = lambda p: _Resp(200, {"proxies": {"a": {"type": "selector"}}})
        self.assertIn("a", self.cl.all_proxies())


if __name__ == "__main__":
    unittest.main()
