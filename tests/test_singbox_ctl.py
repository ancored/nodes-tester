"""Остановка/запуск sing-box и управление службой killswitch роутера."""

import os
import subprocess
import unittest

from nodes_tester.config import SingboxControlConfig
from nodes_tester.singbox_ctl import ControlError, SingboxControl
from tests.helpers import temp_dir

KS_TABLE = """table inet killswitch {
	chain forward_guard {
		type filter hook forward priority filter - 1; policy accept;
		iifname "eth0" oifname "eth1" ip saddr 192.168.1.0/25 meta l4proto tcp counter packets 2511 bytes 103966 reject with tcp reset
		iifname "eth0" oifname "eth1" ip saddr 192.168.1.0/25 counter packets 1566 bytes 594911 reject with icmpx admin-prohibited
	}
}
"""


class FakeRouter:
    """pidof, nft и init-скрипты sing-box/killswitch как на OpenWrt."""

    def __init__(self, init, ks_init):
        self.init, self.ks_init = init, ks_init
        self.running = True
        self.ks_loaded = True
        self.ks_enabled = True
        self.calls = []

    def __call__(self, args, input=None, capture_output=True, text=True, timeout=None):
        self.calls.append(args)
        out, rc = "", 0
        if args[0] == "pidof":
            rc, out = (0, "4242\n") if self.running else (1, "")
        elif args[:4] == ["nft", "list", "table", "inet"]:
            rc, out = (0, KS_TABLE) if self.ks_loaded else (1, "")
        elif args[0] == self.init:
            self.running = args[1] == "start"
        elif args[0] == self.ks_init:
            action = args[1]
            if action == "enabled":
                rc = 0 if self.ks_enabled else 1
            elif action in ("start", "stop"):
                self.ks_loaded = action == "start"
            elif action in ("enable", "disable"):
                self.ks_enabled = action == "enable"
        return subprocess.CompletedProcess(args, rc, out, "")


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, event, text, key=None, ttl=None):
        self.sent.append((event, key, text))


class SingboxControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        init = os.path.join(self.tmp, "sing-box")
        ks = os.path.join(self.tmp, "killswitch")
        for path in (init, ks):
            open(path, "w").close()
        self.cfg = SingboxControlConfig(init=init, killswitch_init=ks,
                                        stop_flag=os.path.join(self.tmp, "run", "stopped"))
        self.router = FakeRouter(init, ks)
        self.notifier = _Notifier()
        self.t = [0.0]
        self.ctl = SingboxControl(self.cfg, self.notifier, run=self.router, clock=lambda: self.t[0])

    def test_pid(self):
        self.assertEqual(self.ctl.pid(), "4242")
        self.router.running = False
        self.assertIsNone(self.ctl.pid())

    def test_killswitch_status_from_service_table(self):
        ks = self.ctl.status()["killswitch"]
        self.assertEqual(ks, {"installed": True, "active": True, "enabled": True, "blocked": 4077})

    def test_stop_and_start_do_not_touch_killswitch(self):
        st = self.ctl.stop()
        self.assertFalse(st["running"])
        self.assertTrue(st["stopped_by_admin"])
        self.assertTrue(st["killswitch"]["active"])          # правила службы остаются
        self.assertIn("killswitch блокирует", self.notifier.sent[-1][2])
        st = self.ctl.start()
        self.assertTrue(st["running"])
        self.assertFalse(st["stopped_by_admin"])
        self.assertFalse(any(c[0] == "nft" and c[1] != "list" for c in self.router.calls))

    def test_toggle_controls_service_and_autostart(self):
        st = self.ctl.set_killswitch(False)
        self.assertEqual((st["killswitch"]["active"], st["killswitch"]["enabled"]), (False, False))
        st = self.ctl.set_killswitch(True)
        self.assertEqual((st["killswitch"]["active"], st["killswitch"]["enabled"]), (True, True))
        actions = [c[1] for c in self.router.calls if c[0] == self.cfg.killswitch_init and c[1] != "enabled"]
        self.assertEqual(actions, ["stop", "disable", "start", "enable"])

    def test_missing_service(self):
        os.remove(self.cfg.killswitch_init)
        self.assertFalse(self.ctl.status()["killswitch"]["installed"])
        with self.assertRaises(ControlError):
            self.ctl.set_killswitch(True)

    def test_crash_is_reported(self):
        self.router.running = False                      # упал сам, не из админки
        self.ctl._check_down()
        self.t[0] = 61
        self.ctl._check_down()
        self.router.running = True
        self.ctl._check_down()
        self.assertEqual([k for _e, k, _t in self.notifier.sent], ["singbox-down", "singbox-up"])
        self.assertIn("killswitch блокирует", self.notifier.sent[0][2])

    def test_unavailable_off_openwrt(self):
        self.cfg.init = os.path.join(self.tmp, "missing")
        self.assertFalse(self.ctl.status()["available"])
        with self.assertRaises(ControlError):
            self.ctl.stop()


if __name__ == "__main__":
    unittest.main()
