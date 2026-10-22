"""Общая авария: прямой выход не отвечает — авария WAN; много аварийных замен, прямой жив, а
ноды разных поставщиков молчат — авария нод; конец — recover удачных проверок подряд. Во время
аварии замеры, резерв и замены стоят."""

import contextlib
import io
import unittest

from nodes_tester.config import OutageConfig
from nodes_tester.outage import OutageGuard
from tests.helpers import make_runner, temp_dir


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def outage(self, kind, detail, minutes=None):
        self.sent.append((kind, minutes is None))


class OutageGuardTest(unittest.TestCase):
    def setUp(self):
        self.up = {"77.88.8.8:443": True, "vk.com:443": True, "www.gosuslugi.ru:443": True}
        self.nodes_alive = True
        self.notifier = FakeNotifier()
        self.guard = OutageGuard(OutageConfig(interval=60, recover=2), "", notifier=self.notifier,
                                 node_alive=lambda leaf: self.nodes_alive,
                                 node_pool=lambda: ["A", "B", "C", "D"],
                                 connect=lambda target: self.up[target])

    def step(self, now):
        with contextlib.redirect_stdout(io.StringIO()):
            self.guard.step(now)

    def test_wan_down_by_quorum_and_back(self):
        self.up["vk.com:443"] = False
        self.step(100)
        self.assertIsNone(self.guard.active)           # одна цель из трёх — не авария
        self.up["77.88.8.8:443"] = False
        self.step(200)
        self.assertEqual(self.guard.active["kind"], "wan")
        self.assertEqual(self.notifier.sent, [])       # без WAN не отправить — пишем по окончании
        self.up.update({"vk.com:443": True, "77.88.8.8:443": True})
        self.step(230)
        self.assertIsNotNone(self.guard.active)        # нужно recover=2 удачи подряд
        self.step(260)
        self.assertIsNone(self.guard.active)
        self.assertEqual(self.notifier.sent, [("wan", False)])

    def test_background_check_waits_for_interval(self):
        self.step(100)
        self.up = dict.fromkeys(self.up, False)
        self.step(130)                                  # раньше interval — не проверяли
        self.assertIsNone(self.guard.active)
        self.step(161)
        self.assertEqual(self.guard.active["kind"], "wan")

    def test_many_emergencies_check_nodes(self):
        for _ in range(2):
            self.guard.note_switch("emergency")
        self.guard.note_switch("rotation")             # не авария — не в счёт
        self.assertFalse(self.guard._suspect)
        self.nodes_alive = False
        self.guard.note_switch("emergency")
        self.step(10)
        self.assertEqual(self.guard.active["kind"], "nodes")
        self.assertEqual(self.notifier.sent, [("nodes", True)])

    def test_emergencies_with_live_nodes_are_not_outage(self):
        for _ in range(3):
            self.guard.note_switch("emergency")
        self.step(10)
        self.assertIsNone(self.guard.active)

    def test_nodes_probe_at_most_configured(self):
        probed = []
        self.guard.node_alive = lambda leaf: probed.append(leaf) and False
        ok, _detail = self.guard.nodes()
        self.assertFalse(ok)
        self.assertEqual(probed, ["A", "B", "C"])       # nodes=3 по умолчанию


class RunnerPauseTest(unittest.TestCase):
    """Общая авария останавливает то же, что пауза применения конфига."""

    def test_outage_pauses(self):
        r = make_runner(temp_dir())
        try:
            self.assertFalse(r._paused())
            r.outage.active = {"kind": "wan", "since": 0, "detail": ""}
            self.assertTrue(r._paused())
            self.assertFalse(r._apply_paused())
            self.assertEqual(r.status()["outage"]["kind"], "wan")
        finally:
            r.storage.close()


if __name__ == "__main__":
    unittest.main()
