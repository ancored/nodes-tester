"""Склейка конфигов sing-box: порядок по приоритетам, дедупликация, конфликты."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodes_admin import merge as merge_mod
from nodes_admin.merge import MergeError, Source, merge


class MergeTest(unittest.TestCase):
    def test_arrays_follow_priority_and_path_overrides(self):
        base = {"route": {"rules": [{"r": "base"}]}, "dns": {"rules": [{"d": "base"}]}}
        ru = {"route": {"rules": [{"r": "ru"}]}, "dns": {"rules": [{"d": "ru"}]}}
        ai = {"route": {"rules": [{"r": "ai1"}, {"r": "ai2"}]}, "dns": {"rules": [{"d": "ai"}]}}
        config, warnings = merge([Source("base", base), Source("ru", ru, 300, {"dns.rules": 150}),
                                  Source("ai", ai, 200)])
        self.assertEqual(config["route"]["rules"], [{"r": "base"}, {"r": "ai1"}, {"r": "ai2"}, {"r": "ru"}])
        self.assertEqual(config["dns"]["rules"], [{"d": "base"}, {"d": "ru"}, {"d": "ai"}])
        self.assertEqual(warnings, [])

    def test_equal_priority_keeps_given_order(self):
        config, _ = merge([Source("a", {"x": [1]}, 5), Source("b", {"x": [2]}, 5)])
        self.assertEqual(config["x"], [1, 2])

    def test_tagged_duplicates(self):
        server = {"tag": "dns-ru", "server": "1.1.1.1"}
        config, warnings = merge([Source("a", {"dns": {"servers": [server]}}, 1),
                                  Source("b", {"dns": {"servers": [dict(server), {"tag": "x"}]}}, 2)])
        self.assertEqual(config["dns"]["servers"], [server, {"tag": "x"}])
        self.assertEqual(warnings, [])
        with self.assertRaisesRegex(MergeError, "dns.servers: тег dns-ru в a и b"):
            merge([Source("a", {"dns": {"servers": [server]}}, 1),
                   Source("b", {"dns": {"servers": [{"tag": "dns-ru", "server": "8.8.8.8"}]}}, 2)])

    def test_untagged_duplicates_dropped_with_warning(self):
        config, warnings = merge([Source("a", {"rules": [{"r": 1}]}, 1),
                                  Source("b", {"rules": [{"r": 1}, {"r": 2}]}, 2)])
        self.assertEqual(config["rules"], [{"r": 1}, {"r": 2}])
        self.assertEqual(warnings, ["rules: повтор элемента из b (уже есть в a) пропущен"])

    def test_scalars(self):
        config, warnings = merge([Source("base", {"route": {"final": "global"}}),
                                  Source("p", {"route": {"final": "direct"}}, 1)])
        self.assertEqual(config["route"]["final"], "global")
        self.assertEqual(warnings, ["route.final: значение из p не применено, действует base"])
        config, _ = merge([Source("low", {"s": "b"}, 9), Source("high", {"s": "a"}, 1)])
        self.assertEqual(config["s"], "a")
        config, warnings = merge([Source("a", {"s": 1}, 1), Source("b", {"s": 1}, 1)])
        self.assertEqual((config["s"], warnings), (1, []))
        with self.assertRaisesRegex(MergeError, "s: a и b с одинаковым приоритетом"):
            merge([Source("a", {"s": 1}, 1), Source("b", {"s": 2}, 1)])

    def test_type_conflict(self):
        with self.assertRaisesRegex(MergeError, "route: разные типы"):
            merge([Source("a", {"route": {}}), Source("b", {"route": []}, 1)])

    def test_key_order_starts_with_base(self):
        config, _ = merge([Source("base", {"log": {}, "dns": {}}), Source("p", {"route": {}, "dns": {}}, 1)])
        self.assertEqual(list(config), ["log", "dns", "route"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, out = Path(d, "a.json"), Path(d, "b.json"), Path(d, "out.json")
            a.write_text(json.dumps({"outbounds": [{"tag": "x"}], "final": "a"}), encoding="utf-8")
            b.write_text(json.dumps({"outbounds": [{"tag": "y"}], "final": "b"}), encoding="utf-8")
            with patch("sys.stderr.write"):
                self.assertEqual(merge_mod.main([str(out), str(a), str(b)]), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                             {"outbounds": [{"tag": "x"}, {"tag": "y"}], "final": "a"})
            b.write_text(json.dumps({"outbounds": [{"tag": "x", "v": 1}]}), encoding="utf-8")
            with patch("sys.stderr.write"):
                self.assertEqual(merge_mod.main([str(out), str(a), str(b)]), 1)


if __name__ == "__main__":
    unittest.main()
