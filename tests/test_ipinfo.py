"""Тип выходного IP: разбор ответа ip-api.com, кэш в БД, показ в результатах."""

import json
import os
import time
import unittest
from types import SimpleNamespace

from dashboard import data
from nodes_tester import ipinfo
from nodes_tester.config import StorageConfig
from nodes_tester.storage import Storage
from tests.helpers import temp_dir


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.rows)


ROWS = [
    {"status": "success", "query": "1.1.1.1", "countryCode": "de", "isp": "Hetzner",
     "org": "Hetzner Online", "as": "AS24940 Hetzner Online GmbH", "asname": "HETZNER-AS",
     "mobile": False, "proxy": True, "hosting": True},
    {"status": "success", "query": "2.2.2.2", "countryCode": "NL", "isp": "KPN",
     "org": "", "as": "AS1136 KPN B.V.", "asname": "KPN", "mobile": False, "proxy": False,
     "hosting": False},
    {"status": "fail", "query": "10.0.0.1", "message": "private range"},
]


class IpInfoTest(unittest.TestCase):
    def test_lookup_parses_and_skips_failures(self):
        s = _Session(ROWS)
        out = ipinfo.lookup(["1.1.1.1", "2.2.2.2", "10.0.0.1", "1.1.1.1", None], "http://x", session=s)
        self.assertEqual(s.calls, [["1.1.1.1", "2.2.2.2", "10.0.0.1"]])
        self.assertEqual(out["1.1.1.1"]["asn"], 24940)
        self.assertEqual(out["1.1.1.1"]["country"], "DE")
        self.assertEqual(ipinfo.kind(out["1.1.1.1"]), "hosting")
        self.assertEqual(ipinfo.kind(out["2.2.2.2"]), "residential")
        self.assertNotIn("10.0.0.1", out)

    def test_cache_and_results_column(self):
        tmp = temp_dir()
        db = os.path.join(tmp, "s.db")
        st = Storage(StorageConfig(db_file=db))
        try:
            self.assertEqual(st.ip_info_stale(["1.1.1.1"], 3600), ["1.1.1.1"])
            st.save_ip_info(ipinfo.lookup(["1.1.1.1"], "http://x", session=_Session(ROWS[:1])))
            self.assertEqual(st.ip_info_stale(["1.1.1.1", "2.2.2.2"], 3600), ["2.2.2.2"])
            st.add_results({"id": "abcd1234", "round": 1, "tests": {
                "connectivity": {"ok": True, "exit_ip": "1.1.1.1", "country": "NL"}}})
        finally:
            st.close()
        rows = data._results(db)["rows"]
        self.assertEqual(rows[0]["ip_type"], "ДЦ, прокси, по базе DE")
        self.assertEqual(rows[0]["ip_asn"], "AS24940 HETZNER-AS")
        self.assertEqual(rows[0]["ip_title"], "1.1.1.1 · AS24940 HETZNER-AS · Hetzner Online")


if __name__ == "__main__":
    unittest.main()
