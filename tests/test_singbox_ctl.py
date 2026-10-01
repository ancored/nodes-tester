"""Остановка/запуск sing-box и killswitch: команды init/nft, флаг остановки, сторож."""

import os
import subprocess
import unittest

from nodes_tester.config import SingboxControlConfig
from nodes_tester.singbox_ctl import TABLE, ControlError, SingboxControl
from tests.helpers import temp_dir


class FakeRouter:
    """pidof/nft/init.d как на OpenWrt: состояние процесса и таблицы nftables."""

    def __init__(self):
        self.running = True
        self.table = False
        self.calls = []
        self.rules = None

    def __call__(self, args, input=None, capture_output=True, text=True, timeout=None):
        self.calls.append(args)
        rc = 0
        if args[0] == "pidof":
            rc = 0 if self.running else 1
        elif args[:3] == ["nft", "list", "table"]:
            rc = 0 if self.table else 1
        elif args[:2] == ["nft", "-f"]:
            self.table, self.rules = True, input
        elif args[:3] == ["nft", "delete", "table"]:
            self.table = False
        elif args[0] == "ubus":
            return subprocess.CompletedProcess(args, 0, '{"l3_device": "eth0"}', "")
        elif args[-1] == "stop":
            self.running = False
        elif args[-1] == "start":
            self.running = True
        return subprocess.CompletedProcess(args, rc, "", "")


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, event, text, key=None, ttl=None):
        self.sent.append((event, key))


class SingboxControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        init = os.path.join(self.tmp, "sing-box")
        open(init, "w").close()
        self.cfg = SingboxControlConfig(init=init, stop_flag=os.path.join(self.tmp, "run", "stopped"),
                                        lan_devices=["br-lan", "bad;dev"], down_alert=60)
        self.router = FakeRouter()
        self.notifier = _Notifier()
        self.t = [0.0]
        self.ctl = self._ctl()

    def _ctl(self):
        return SingboxControl(self.cfg, os.path.join(self.tmp, "state.json"), self.notifier,
                              run=self.router, clock=lambda: self.t[0])

    def test_stop_without_killswitch_goes_direct(self):
        st = self.ctl.stop()
        self.assertFalse(st["running"])
        self.assertTrue(st["stopped_by_admin"])
        self.assertFalse(st["blocking"])                 # прямой интернет
        st = self.ctl.start()
        self.assertTrue(st["running"])
        self.assertFalse(st["stopped_by_admin"])

    def test_killswitch_blocks_only_while_down(self):
        self.ctl.set_killswitch(True)
        self.assertFalse(self.router.table)              # sing-box работает — блока нет
        self.ctl.stop()
        self.assertTrue(self.router.table)
        self.assertIn('iifname { "br-lan" } counter drop', self.router.rules)
        self.assertNotIn("bad;dev", self.router.rules)   # некорректное имя не попадает в nft
        self.ctl.start()
        self.assertFalse(self.router.table)
        # Режим переживает перезапуск тестера.
        self.assertTrue(self._ctl().killswitch)

    def test_crash_is_blocked_and_reported(self):
        self.ctl.set_killswitch(True)
        self.router.running = False                      # упал сам, не из админки
        self.ctl.enforce()
        self.ctl._check_down()
        self.assertTrue(self.router.table)
        self.t[0] = 61
        self.ctl._check_down()
        self.router.running = True
        self.ctl.enforce()
        self.ctl._check_down()
        self.assertFalse(self.router.table)
        keys = [k for _e, k in self.notifier.sent]
        self.assertEqual(keys[-2:], ["singbox-down", "singbox-up"])

    def test_unavailable_off_openwrt(self):
        self.cfg.init = os.path.join(self.tmp, "missing")
        ctl = self._ctl()
        self.assertFalse(ctl.status()["available"])
        with self.assertRaises(ControlError):
            ctl.stop()

    def test_lan_device_autodetected(self):
        self.cfg.lan_devices = []
        ctl = self._ctl()
        ctl.set_killswitch(True)
        ctl.stop()
        self.assertIn('iifname { "eth0" }', self.router.rules)

    def test_table_name(self):
        self.assertEqual(TABLE, "nodes_tester_killswitch")


if __name__ == "__main__":
    unittest.main()
