"""Очередь тестера по срокам годности: новые ноды — сразу, истёкший тест ждёт, пока новый
результат доживёт до ротации, за одно обращение — тесты, которым до срока меньше разброса;
host-aware выбор (дольше всех не тронутый хост, зазор host_gap); сверка состава нод."""

import time
import unittest
from unittest.mock import patch

from nodes_tester.config import TESTS, GroupConfig
from nodes_tester.freshness import expires
from nodes_tester.identity import parse_node
from tests.helpers import make_runner, temp_dir

A, B = "DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-de-out [aaaa0002]"
U = "DEMO-vless-us-out [bbbb0001]"
NEW = "DEMO-vless-fr-out [cccc0001]"
HOUR = 3600


class _Base(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), tags=[A, B, U, NEW], groups=[{"tag": "eu"}, {"tag": "us"}],
                             tests={name: {"ttl": "1h", "ttl_jitter": "6m"} for name in TESTS})
        r = self.r
        r.board.set_groups({"eu": {A, B}, "us": {U}})
        r._node_by_raw = {raw: (frozenset(), parse_node(raw)) for raw in (A, B, U, NEW)}
        self.now = time.time()

    def tearDown(self):
        self.r.storage.close()

    def _measured(self, raw, ago, tests=TESTS):
        """Результаты тестов ноды `ago` секунд назад."""
        crc = parse_node(raw).node_id
        self.r._latest.setdefault(crc, {}).update({t: (int(self.now - ago), {"ok": True}) for t in tests})

    def _rotation(self, group, in_seconds):
        self.r.switcher.state[group] = {"active": A if group == "eu" else U,
                                        "rotate_deadline": self.now + in_seconds,
                                        "last_switch": self.now - 2 * HOUR}

    def _plan(self, raw):
        return self.r._plan(raw, parse_node(raw), self.now)


class UselessNodeTest(_Base):
    """Нода, которой не подходит ни одна группа (страна выхода): только связность раз в сутки."""

    def setUp(self):
        super().setUp()
        day = {"ttl": 24 * HOUR, "ttl_jitter": 0}
        self.r.board.set_policy([GroupConfig("eu", require={"country": {"regions": ["eu"], **day}}),
                                 GroupConfig("us", require={"country": {"regions": ["us"], **day}})],
                                self.r.cfg.regions)
        self.r.cfg.checks["country"] = day
        self.wl = GroupConfig("whitelist", require={"whitelist": day})

    def _country(self, raw, cc, ago):
        self.r.board.rows[raw] = {"node": raw, "id": parse_node(raw).node_id, "provider": "DEMO",
                                  "exit_cc": cc, "country_ts": int(self.now - ago), "score": 0}

    def test_foreign_to_every_group_waits_for_country_expiry(self):
        self._country(A, "RU", ago=2 * HOUR)
        self._measured(A, ago=2 * HOUR)                       # остальные тесты истекли
        start, tests = self._plan(A)
        self.assertEqual(tests, [])
        self.assertAlmostEqual(start, self.now - 2 * HOUR + 23 * HOUR, delta=1)

    def test_only_connectivity_before_country_expires(self):
        self._country(A, "RU", ago=23.5 * HOUR)
        self._measured(A, ago=2 * HOUR)
        self.assertEqual(self._plan(A)[1], ["connectivity"])

    def test_node_fitting_a_group_runs_everything(self):
        self._country(A, "NL", ago=2 * HOUR)
        self._measured(A, ago=2 * HOUR)
        self.assertEqual(self._plan(A)[1], list(TESTS))

    def test_unknown_country_runs_everything(self):
        self._country(A, "", ago=2 * HOUR)
        self._measured(A, ago=2 * HOUR)
        self.assertEqual(self._plan(A)[1], list(TESTS))

    def test_client_group_keeps_the_node(self):
        self._country(A, "RU", ago=2 * HOUR)
        self._measured(A, ago=2 * HOUR)
        self.r.board.set_required_result(A, "whitelist", True)
        self.r._client_groups = [(self.wl, {"DEMO"})]
        self.r.cfg.pipeline.clients.enabled = True
        self.assertEqual(self._plan(A)[1], list(TESTS))
        self.r.cfg.pipeline.clients.enabled = False           # ветвь клиентов выключена
        self.assertEqual(self._plan(A)[1], [])
        self.r.cfg.pipeline.clients.enabled = True
        self.r.board.set_required_result(A, "whitelist", False)
        self.assertEqual(self._plan(A)[1], [])


class PlanTest(_Base):
    def test_new_node_due_now_despite_rotation(self):
        self._rotation("eu", 3 * HOUR)
        start, tests = self._plan(NEW)
        self.assertLessEqual(start, self.now)
        self.assertEqual(tests, list(TESTS))

    def test_fresh_node_waits_for_expiry(self):
        self._measured(U, ago=60)
        start, tests = self._plan(U)
        self.assertEqual(tests, [])
        self.assertGreater(start, self.now + 50 * 60)

    def test_expired_runs_regardless_of_rotation(self):
        self._measured(A, ago=2 * HOUR)                 # истёк
        self._rotation("eu", 3 * HOUR)
        start, tests = self._plan(A)
        self.assertEqual(tests, list(TESTS))
        self.assertLess(start, self.now)

    def test_only_due_tests_join_and_connectivity_always(self):
        self._measured(U, ago=10 * 60)                         # всё свежее…
        self._measured(U, ago=2 * HOUR, tests=("download",))   # …кроме download
        self.assertEqual(self._plan(U)[1], ["connectivity", "download"])

    def test_tests_near_expiry_join(self):
        self._measured(A, ago=55 * 60)                         # до срока меньше разброса
        self._measured(A, ago=2 * HOUR, tests=("download",))
        self.assertEqual(self._plan(A)[1], list(TESTS))

    def test_forced_runs_everything(self):
        self._measured(U, ago=60)
        self.r._forced = {U}
        self.assertEqual(self._plan(U)[1], list(TESTS))


class JitterTest(unittest.TestCase):
    def test_expiry_within_jitter_and_stable(self):
        ts = 1_000_000
        exp = [expires(ts, 3600, 360, f"n{i}/latency") for i in range(50)]
        self.assertTrue(all(ts + 3240 <= e <= ts + 3600 for e in exp))
        self.assertGreater(len(set(exp)), 40)                  # сроки расходятся
        self.assertEqual(exp[0], expires(ts, 3600, 360, "n0/latency"))
        self.assertEqual(expires(0, 3600, 360, "x"), 0)        # не мерили — истёк


class PickTest(_Base):
    def setUp(self):
        super().setUp()
        r = self.r
        r._node_by_raw = {raw: (frozenset(), parse_node(raw)) for raw in (A, B, U)}
        r._endpoints = {"aaaa0001": ("h1", 443), "aaaa0002": ("h1", 8443), "bbbb0001": ("h2", 443)}
        r.cfg.tester.host_gap = 120

    def test_least_recently_contacted_host_first(self):
        r = self.r
        r._host_ep_last = {"h1": {r._ep_sig(parse_node(A)): time.monotonic()}}
        self.assertEqual(r._pick(self.now)[1].raw, U)

    def test_oldest_result_first(self):
        self._measured(A, ago=2 * HOUR)
        self._measured(U, ago=3 * HOUR)
        self._measured(B, ago=90 * 60)
        self.assertEqual(self.r._pick(self.now)[1].raw, U)

    def test_active_and_reserve_before_backlog(self):
        r = self.r
        self._measured(U, ago=3 * HOUR)                         # самая старая, но не выбрана
        self._measured(B, ago=90 * 60)
        self._measured(A, ago=70 * 60)
        r.board.set_reserve("eu", [B])
        self.assertEqual(r._pick(self.now)[1].raw, B)
        r.board.set_reserve("eu", [])
        self._rotation("eu", HOUR)                              # A — активная eu
        self.assertEqual(r._pick(self.now)[1].raw, A)

    def test_host_gap_blocks_other_endpoint(self):
        r = self.r
        self._measured(U, ago=60)                               # h2 не нужен
        r._host_ep_last = {"h1": {r._ep_sig(parse_node(B)): time.monotonic()}}
        self.assertEqual(r._pick(self.now)[1].raw, B)           # тот же порт — можно
        self._measured(B, ago=60)
        wake = r._pick(self.now)                                # A ждёт зазор
        self.assertIsInstance(wake, float)                      # ждать (шагом не дольше 30 с)
        self.assertGreater(r._host_wait("h1", r._ep_sig(parse_node(A))), 100)

    def test_backed_off_skipped(self):
        r = self.r
        r._backoff = {"bbbb0001": (int(self.now) + HOUR, 1, "backoff")}
        r._forced = {U}
        self.assertNotEqual(r._pick(self.now)[1].raw, U)
        self.assertEqual(r._forced, set())


class MeasureTest(_Base):
    def test_score_uses_latest_result_of_each_test(self):
        r = self.r
        self._measured(A, ago=10 * 60)
        record = {"id": "aaaa0001", "node": A,
                  "tests": {"_select": {"ok": True}, "connectivity": {"ok": True}}}
        seen = {}
        with patch.object(r, "_test_node", return_value=record), \
                patch.object(r, "_respect_host_gap"), \
                patch.object(r, "_score_and_maybe_switch",
                             side_effect=lambda reg, ident, tests: seen.update(tests) or True):
            self.assertTrue(r._measure(frozenset(), parse_node(A), ["connectivity"]))
        self.assertEqual(set(seen), {"_select", *TESTS})
        self.assertGreater(r._latest["aaaa0001"]["connectivity"][0], self.now - 5)

    def test_select_failure_scores_only_this_record(self):
        r = self.r
        self._measured(A, ago=10 * 60)
        record = {"id": "aaaa0001", "node": A, "tests": {"_select": {"ok": False}}}
        seen = {}
        with patch.object(r, "_test_node", return_value=record), \
                patch.object(r, "_respect_host_gap"), \
                patch.object(r, "_box_interrupted", return_value=False), \
                patch.object(r, "_score_and_maybe_switch",
                             side_effect=lambda reg, ident, tests: seen.update(tests) or False):
            r._measure(frozenset(), parse_node(A), TESTS)
        self.assertEqual(set(seen), {"_select"})


class RequestTest(_Base):
    def test_repeated_request_keeps_queue(self):
        r = self.r
        self.assertTrue(r.request_tests())
        r._force_requested.clear()
        r._forced = {B}                             # A уже проверена
        self.assertFalse(r.request_tests())
        self.assertFalse(r._force_requested.is_set())

    def test_node_gone_from_selector_rereads_nodes(self):
        from nodes_tester.api import ApiError

        def select(group, node):
            raise ApiError("not found in selector")
        self.r.api.select = select
        self.assertIsNone(self.r._test_node(frozenset(), parse_node(A), TESTS))
        self.assertTrue(self.r._stale_nodes)


class RefreshTest(unittest.TestCase):
    """Сверка состава: смена селектора сразу видна в базе, новые ноды — в очередь."""

    def setUp(self):
        self.r = make_runner(temp_dir(), tags=[A, B])
        for crc, present in (("aaaa0001", 1), ("aaaa0002", 1), ("cccc0001", None)):
            self.r.storage._db.execute("INSERT INTO nodes (crc, tag, present) VALUES (?,?,?)",
                                       (crc, crc, present))
        self.wakes = []
        self.r.reserve.wake = lambda ratings_changed=False: self.wakes.append(ratings_changed)
        self.r._refresh_nodes()

    def tearDown(self):
        self.r.storage.close()

    def _present(self):
        return dict(self.r.storage._db.execute("SELECT crc, present FROM nodes").fetchall())

    def test_new_node_queued_and_reserve_woken(self):
        self.r.api._tags = [A, NEW]
        self.r._refresh_nodes()
        self.assertEqual(set(self.r._node_by_raw), {A, NEW})
        self.assertEqual(self._present(), {"aaaa0001": 1, "aaaa0002": 0, "cccc0001": 1})
        self.assertEqual(self.wakes[-1], True)

    def test_removed_node_row_pruned(self):
        self.r.board.rows = {B: {"node": B, "id": "aaaa0002", "score": 50}}
        self.r.api._tags = [A]
        self.r._refresh_nodes()
        self.assertNotIn(B, self.r.board.rows)
        self.assertEqual(self.wakes[-1], False)


class HostGapTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir())

    def tearDown(self):
        self.r.storage.close()

    def test_gap_waits_only_on_different_endpoint(self):
        slept = []
        with patch("nodes_tester.runner.time.sleep", side_effect=slept.append):
            r = self.r
            r.cfg.tester.host_gap = 120
            r._respect_host_gap("H", (443, "vless"))   # первый — без сна
            r._respect_host_gap("H", (443, "vless"))   # тот же sig — без сна
            self.assertEqual(slept, [])
            r._respect_host_gap("H", (8443, "trojan"))  # другой sig на том же хосте
            self.assertEqual(len(slept), 1)
            self.assertGreater(slept[0], 100)

    def test_gap_zero_never_sleeps(self):
        slept = []
        with patch("nodes_tester.runner.time.sleep", side_effect=slept.append):
            self.r.cfg.tester.host_gap = 0
            self.r._respect_host_gap("H", (1, "a"))
            self.r._respect_host_gap("H", (2, "b"))
            self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
