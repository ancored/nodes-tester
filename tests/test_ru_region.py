"""Российская нода не получает зарубежный набор тестов: страна — из базы после рестарта,
а у ещё не измеренной ноды остальные тесты замера идут по стране из conn-теста."""

import unittest

from tests.helpers import fresh_nodes_json, make_runner, temp_dir

TAG = "P-vless-out [aaaa0001.00000001]"


class FakeTest:
    def __init__(self, name, result, seen):
        self.name, self.result, self.seen = name, result, seen

    def run(self, ctx):
        self.seen.append((self.name, set(ctx.regions)))
        return type("R", (), {"to_dict": lambda s: dict(self.result)})()


class RuRegionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.r = make_runner(self.tmp, tags=[TAG])
        self.addCleanup(self.r.storage.close)

    def test_country_from_db_after_restart(self):
        self.r.storage.load_nodes(fresh_nodes_json(self.tmp, [{"tag": TAG, "type": "vless", "server": "1.2.3.4"}]))
        self.r.storage.add_results({"id": "aaaa0001.00000001", "node": TAG,
                                    "tests": {"connectivity": {"ok": True, "country": "RU"}}})
        self.r.board.rows = {}                        # рейтинг после рестарта пуст
        [(regions, _ident)] = self.r._enumerate_nodes()
        self.assertEqual(regions, {"ru"})

    def test_unknown_country_rest_of_measure_uses_measured(self):
        seen = []
        self.r._tests_cache = {"connectivity": FakeTest("connectivity", {"ok": True, "country": "RU"}, seen),
                               "latency": FakeTest("latency", {"ok": True}, seen)}
        self.r._originals = {self.r._top: ""}         # выбор селектора уже запомнен
        self.r.cfg.tester.switch_delay = 0
        [(regions, ident)] = self.r._enumerate_nodes()
        record = self.r._test_node(regions, ident, ["connectivity", "latency"])
        self.assertEqual(seen, [("connectivity", set()), ("latency", {"ru"})])
        self.assertEqual(record["regions"], ["ru"])


if __name__ == "__main__":
    unittest.main()
