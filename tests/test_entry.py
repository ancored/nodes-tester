"""Вход ноды: резолв адреса сервера через DoH группы bootstrap, кэш в БД, цепочки «вход → выход» в данных админки."""

import json
import os
import unittest
from unittest.mock import patch

import requests

from dashboard import data
from nodes_tester import entry
from nodes_tester.config import StorageConfig
from nodes_tester.storage import Storage
from tests.helpers import temp_dir


def _doh(url, name, timeout, qtypes):
    if url == "https://dead/dns-query" or name == "down.example":
        raise requests.ConnectionError("no route")
    if name == "gone.example":
        raise ValueError("DNS rcode 3")
    return {"chain.example": ["10.0.0.2", "10.0.0.1", "10.0.0.2"]}[name]


class EntryTest(unittest.TestCase):
    def test_resolve_tries_group_servers_and_skips_failures(self):
        tmp = temp_dir()
        config = os.path.join(tmp, "config.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"dns": {"servers": [
                {"tag": "bootstrap", "type": "group", "servers": ["dead", "ok"]},
                {"tag": "dead", "type": "https", "server": "dead"},
                {"tag": "ok", "type": "https", "server": "1.1.1.1"}]}}, fh)
        with patch.object(entry, "doh_resolve", side_effect=_doh) as doh:
            out = entry.resolve(["chain.example", "gone.example", "down.example", "chain.example", ""], config)
        self.assertEqual(out, {"chain.example": ["10.0.0.1", "10.0.0.2"]})
        calls = [(c.args[:2], c.kwargs) for c in doh.call_args_list if c.args[1] == "chain.example"]
        self.assertEqual(calls, [(("https://dead/dns-query", "chain.example"), {"qtypes": (1,)}),
                                 (("https://1.1.1.1/dns-query", "chain.example"), {"qtypes": (1,)})])

    def test_chain(self):
        info = {"10.0.0.1": {"country": "fi"}, "9.9.9.9": {"asn": 13335, "country": "NL"}}
        self.assertEqual(entry.chain(["10.0.0.1"], "10.0.0.1", "fi", info)["chain"], 0)
        out = entry.chain(["10.0.0.1"], "9.9.9.9", "nl", info)
        self.assertEqual((out["entry_cc"], out["exit_cc"], out["chain"], out["warp"]), ("FI", "NL", 1, 1))
        self.assertEqual(entry.chain([], "9.9.9.9", "nl", info), {})
        self.assertEqual(entry.chain(["10.0.0.1"], "", "", info), {})

    def test_cache_and_admin_rows(self):
        db = os.path.join(temp_dir(), "s.db")
        st = Storage(StorageConfig(path=db))
        try:
            st._db.executemany("INSERT INTO nodes (crc, server) VALUES (?, ?)",
                               [("a.1", "chain.example"), ("b.1", "10.0.0.5"), ("c.1", "new.example")])
            st._db.executemany("INSERT INTO scores (crc, node, exit_ip, exit_cc) VALUES (?, ?, ?, ?)",
                               [("a.1", "A", "9.9.9.9", "NL"), ("b.1", "B", "10.0.0.5", "DE"),
                                ("c.1", "C", "8.8.8.8", "US")])
            st._db.commit()
            self.assertEqual(st.entry_stale(["chain.example", "new.example"], 3600),
                             ["chain.example", "new.example"])
            st.save_entry({"chain.example": ["10.0.0.1", "10.0.0.2"]})
            st.save_ip_info({"10.0.0.2": {"country": "FI"}, "9.9.9.9": {"asn": 13335, "as_name": "CLOUDFLARENET", "country": "NL"}})
            self.assertEqual(st.entry_stale(["chain.example", "new.example"], 3600), ["new.example"])
            self.assertEqual(st.load_entry(), {"chain.example": ["10.0.0.1", "10.0.0.2"]})
        finally:
            st.close()
        chains = data._chains(db)
        self.assertEqual((chains["a.1"]["chain"], chains["a.1"]["route"]), ("FI→NL WARP", "FI→NL WARP"))
        self.assertEqual(chains["a.1"]["entry_ip"], "10.0.0.1, 10.0.0.2")
        self.assertEqual((chains["a.1"]["entry_as"], chains["a.1"]["exit_as"]), ("", "AS13335 CLOUDFLARENET"))
        self.assertNotIn("chain", chains["b.1"])
        self.assertEqual(chains["b.1"]["route"], "DE")
        self.assertNotIn("c.1", chains)          # вход ещё не резолвлен


if __name__ == "__main__":
    unittest.main()
