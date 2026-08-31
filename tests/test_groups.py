"""Уплощённая генерация групп: ноды — прямые члены {region}-auto-out (+failsafe),
без leaf/провайдер-групп и {region}-nodes-tester; пустой требуемый регион заполняется
фолбэком (конфиг sing-box не падает)."""

import os
import sys
import unittest

# subscribe использует плоские импорты — добавляем его в путь, как __main__.py
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "subscribe"))
import groups  # noqa: E402


def _node(prov, proto, cc, crc):
    return {"tag": f"{prov}-{cc}-{proto} [{crc}]",
            "_meta": {"provider": prov, "protocol": proto, "country": cc}}


class GroupsFlatTest(unittest.TestCase):
    def setUp(self):
        groups.load_params()          # config/ этого проекта

    def _build(self, nodes):
        return {o["tag"]: o for o in groups.build(nodes)}

    def test_flat_region_members_are_nodes(self):
        out = self._build([_node("LUNA", "vless(reality)", "nl", "a1"),
                           _node("LUNA", "trojan", "nl", "a2")])
        eu = out["eu-auto-out"]
        self.assertEqual(eu["outbounds"][0], "eu-auto-out-failsafe")
        self.assertIn("LUNA-nl-vless(reality) [a1]", eu["outbounds"])
        self.assertEqual(out["eu-auto-out-failsafe"]["type"], "urltest")
        self.assertIn("LUNA-nl-trojan [a2]", out["eu-auto-out-failsafe"]["outbounds"])

    def test_no_leaf_or_region_tester_groups(self):
        out = self._build([_node("LUNA", "vless(reality)", "nl", "a1")])
        leafish = [t for t in out if "-out" in t and "-auto-out" not in t]
        self.assertEqual(leafish, [])
        self.assertNotIn("eu-nodes-tester", out)
        self.assertIn("nodes-tester", out)          # плоский тестовый селектор

    def test_empty_region_filled_with_fallback(self):
        out = self._build([_node("LUNA", "vless(reality)", "nl", "a1")])  # только eu
        for tag in ("eu-auto-out", "us-auto-out", "other-auto-out", "global-auto-out"):
            self.assertIn(tag, out)
            self.assertTrue(out[tag]["outbounds"])
        self.assertIn("LUNA-nl-vless(reality) [a1]", out["us-auto-out"]["outbounds"])

    def test_no_empty_groups_or_dupes(self):
        tags = [o["tag"] for o in groups.build(
            [_node("LUNA", "vless(reality)", "nl", "a1"),
             _node("VSPACE", "vless(reality)", "us", "a2")])]
        self.assertEqual(len(tags), len(set(tags)))   # нет дублей


if __name__ == "__main__":
    unittest.main()
