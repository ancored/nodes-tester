"""groups[].rotation: группа выключает плановую ротацию или берёт свой интервал, остальные
поля ротации и другие группы остаются на selection.rotation."""

import time
import unittest

from nodes_tester.config import ConfigError, parse_config
from tests.helpers import make_runner, temp_dir

EU = ["DEMO-vless-nl-out [aaaa0001]", "DEMO-vless-nl-out [aaaa0002]"]
US = ["DEMO-vless-us-out [bbbb0001]", "DEMO-vless-us-out [bbbb0002]"]


def _row(node, region):
    return {"node": node, "region": region, "score": 70.0, "provider": "DEMO",
            "protocol": "vless", "country": "", "label": "", "id": node[-9:-1],
            "active": 0, "samples": 5, "heavy_ok": "", "heavy_ts": 0}


class GroupRotationParseTest(unittest.TestCase):
    def test_default_is_selection_rotation(self):
        cfg = parse_config({"selection": {"rotation": {"interval": "2h", "min_dwell": "10m"}},
                            "groups": [{"tag": "eu"}]})
        self.assertEqual(cfg.groups[0].rotation, cfg.selection.rotation)

    def test_override_enabled_and_interval(self):
        cfg = parse_config({"selection": {"rotation": {"interval": "2h", "jitter": "5m", "spread": "7m"}},
                            "groups": [{"tag": "ai", "rotation": {"enabled": False}},
                                       {"tag": "eu", "rotation": {"interval": "1h"}}]})
        ai, eu = cfg.groups
        self.assertFalse(ai.rotation.enabled)
        self.assertEqual((ai.rotation.interval, ai.rotation.jitter), (7200.0, 300.0))
        self.assertTrue(eu.rotation.enabled)
        self.assertEqual((eu.rotation.interval, eu.rotation.jitter), (3600.0, 300.0))
        self.assertEqual(eu.rotation.spread, 420.0)              # прочее — общее
        self.assertTrue(cfg.selection.rotation.enabled)

    def test_unknown_key(self):
        with self.assertRaisesRegex(ConfigError, r"groups\[0\]\.rotation\.spread: неизвестное поле \(есть: "):
            parse_config({"groups": [{"tag": "ai", "rotation": {"spread": "5m"}}]})

    def test_client_group_rejects_rotation(self):
        with self.assertRaisesRegex(ConfigError, "rotation: неизвестное поле"):
            parse_config({"pipeline": {"clients": {"groups": [{"tag": "a", "rotation": {"enabled": False}}]}}})


class GroupRotationSwitcherTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(temp_dir(), groups=[
            {"tag": "eu"},
            {"tag": "us", "rotation": {"enabled": False}},
            {"tag": "ai", "rotation": {"interval": "1h", "jitter": "0s"}}])
        sw, board = self.r.switcher, self.r.board
        sw.api = self.r.api
        groups = {"eu": EU, "us": US}
        board.set_groups({g: set(m) for g, m in groups.items()})
        board.rows = {m: _row(m, g) for g, ms in groups.items() for m in ms}
        self.r.api.all_proxies = lambda: {f"{g}-auto-out": {"type": "Selector", "all": list(m)}
                                          for g, m in groups.items()}
        self.now = time.time()
        sw.state = {g: {"active": m[0], "rotate_deadline": self.now - 1, "last_switch": self.now - 7200}
                    for g, m in groups.items()}
        for g, m in groups.items():
            board.set_reserve(g, m[1:])

    def tearDown(self):
        self.r.storage.close()

    def test_disabled_group_never_due(self):
        sw = self.r.switcher
        self.assertIsNone(sw.rotate_deadline("us"))
        self.assertFalse(sw.rotation_due("us"))
        self.assertTrue(sw.rotation_due("eu"))
        self.assertIsNotNone(sw.rotate_deadline("eu"))
        self.assertEqual(sw.next_rotate_deadline(), sw.rotate_deadline("eu"))

    def test_rotation_branch_skips_disabled_group(self):
        sw = self.r.switcher
        sw.rotate_due()
        self.assertEqual(sw.state["us"]["active"], US[0])
        self.assertNotEqual(sw.state["eu"]["active"], EU[0])      # включённая группа ротируется
        sw.evaluate_region("us")
        self.assertEqual(sw.state["us"]["active"], US[0])

    def test_next_deadline_uses_group_interval(self):
        sw = self.r.switcher
        self.assertEqual(sw._next_deadline(1000.0, "ai"), 1000.0 + 3600)
        self.assertLessEqual(abs(sw._next_deadline(1000.0, "eu") - (1000.0 + 6 * 3600)), 900)
        self.assertLessEqual(abs(sw._next_deadline(1000.0) - (1000.0 + 6 * 3600)), 900)


if __name__ == "__main__":
    unittest.main()
