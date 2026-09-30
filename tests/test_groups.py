"""Уплощённая генерация групп: ноды — прямые члены {region}-auto-out (+failsafe),
без leaf/провайдер-групп и {region}-nodes-tester; пустой требуемый регион заполняется
фолбэком (конфиг sing-box не падает)."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from nodes_config import groups, params  # noqa: E402


def _node(prov, proto, cc, crc):
    return {"tag": f"{prov}-{cc}-{proto} [{crc}]",
            "_meta": {"provider": prov, "protocol": proto, "country": cc}}


class GroupsFlatTest(unittest.TestCase):
    def setUp(self):
        self.params = params.load_path(os.path.join(_ROOT, "config", "groups_params.json"))

    def _groups(self, nodes):
        return groups.build(nodes, selector=self.params["selector"], urltest=self.params["urltest"],
                            emit=self.params["emit"], log=lambda m: None)

    def _build(self, nodes):
        return {o["tag"]: o for o in self._groups(nodes)}

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
        tags = [o["tag"] for o in self._groups(
            [_node("LUNA", "vless(reality)", "nl", "a1"),
             _node("VSPACE", "vless(reality)", "us", "a2")])]
        self.assertEqual(len(tags), len(set(tags)))   # нет дублей


def _lnode(prov, cc, crc, label=""):
    return {"tag": f"{prov}-vless|reality-{cc}-out [{crc}]",
            "_meta": {"provider": prov, "protocol": "vless|reality", "country": cc, "label": label}}


NODES = [_lnode("A", "nl", "01"), _lnode("A", "us", "02", "AI"), _lnode("B", "jp", "03"),
         _lnode("B", "tr", "04", "AI"), _lnode("B", "de", "05", "Media")]


class ConfigurableGroupsTest(unittest.TestCase):
    def _out(self, groups_def, regions=None, emit=None):
        p = params.load({"groups": groups_def, "regions": regions or {}, "emit": emit or {}})
        return {o["tag"]: o for o in groups.build(NODES, emit=p["emit"], groups=p["groups"],
                                                  regions=p["regions"], log=lambda m: None)}

    def _members(self, out, name):
        return [t for t in out[f"{name}-auto-out"]["outbounds"] if not t.endswith("-failsafe")]

    def test_explicit_legacy_groups_equal_default(self):
        legacy = groups.build(NODES, log=lambda m: None)
        explicit = [{"name": r, "match": {"regions": [r]}, "fallback": r != "ru"}
                    for r in ("eu", "us", "ru", "other")]
        p = params.load({"groups": explicit})
        self.assertEqual(groups.build(NODES, groups=p["groups"], log=lambda m: None), legacy)

    def test_disabled_group_and_tester_membership(self):
        out = self._out([{"name": "eu", "match": {"regions": ["eu"]}},
                         {"name": "other", "enabled": False, "match": {"regions": ["other"]}}])
        self.assertNotIn("other-auto-out", out)
        self.assertEqual(out["global-auto-out"]["outbounds"], ["eu-auto-out"])
        tester = out["nodes-tester"]["outbounds"]
        self.assertIn("B-vless|reality-tr-out [04]", tester)      # tr — европейская страна
        self.assertNotIn("B-vless|reality-jp-out [03]", tester)   # не попала ни в одну группу

    def test_overlapping_ai_group_not_in_global(self):
        out = self._out([{"name": "eu", "match": {"regions": ["eu"]}},
                         {"name": "ai", "in_global": False,
                          "match": {"labels": ["AI"], "exclude_countries": ["tr"]}}])
        self.assertEqual(self._members(out, "ai"), ["A-vless|reality-us-out [02]"])
        self.assertEqual(out["global-auto-out"]["outbounds"], ["eu-auto-out"])

    def test_and_between_conditions_custom_region(self):
        out = self._out([{"name": "asia", "match": {"regions": ["asia"]}},
                         {"name": "euai", "match": {"regions": ["eu"], "labels": ["AI"]}}],
                        regions={"asia": ["JP", "sg"]})
        self.assertEqual(self._members(out, "asia"), ["B-vless|reality-jp-out [03]"])
        self.assertEqual(self._members(out, "euai"), ["B-vless|reality-tr-out [04]"])

    def test_overridden_eu_changes_other(self):
        out = self._out([{"name": "other", "match": {"regions": ["other"]}}],
                        regions={"eu": ["nl", "de"]})
        self.assertIn("B-vless|reality-tr-out [04]", self._members(out, "other"))

    def test_empty_group_fallback_flag(self):
        out = self._out([{"name": "eu", "match": {"regions": ["eu"]}},
                         {"name": "za", "match": {"countries": ["za"]}},
                         {"name": "zb", "fallback": False, "match": {"countries": ["zb"]}}])
        self.assertEqual(len(self._members(out, "za")), len(NODES))
        self.assertNotIn("zb-auto-out", out)

    def test_params_validation(self):
        for bad in ([{"name": "Eu"}], [{"name": "global"}], [{"name": "a"}, {"name": "a"}],
                    [{"name": "a", "match": {"regions": ["mars"]}}],
                    [{"name": "a", "match": {"colour": ["x"]}}], [{"name": "a", "enabled": "yes"}]):
            with self.assertRaises(params.ParamsError, msg=bad):
                params.load({"groups": bad})


if __name__ == "__main__":
    unittest.main()
