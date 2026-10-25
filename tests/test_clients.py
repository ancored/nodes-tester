"""Клиенты в config.json: имена и токены ссылок; список для build-clients.sh и состояние для админки."""

import contextlib
import io
import json
import os
import unittest

from nodes_admin import clients
from nodes_tester.config import ConfigError, parse_config
from tests.helpers import temp_dir

TOKEN = "Abcdefghij_klmnop-1"


class ClientsConfigTest(unittest.TestCase):
    def test_defaults_and_errors(self):
        cfg = parse_config({"clients": [{"name": "phone", "token": TOKEN},
                                        {"name": "work-pc", "token": TOKEN + "x", "enabled": False}]})
        self.assertEqual(cfg.clients, [{"name": "phone", "token": TOKEN, "enabled": True},
                                       {"name": "work-pc", "token": TOKEN + "x", "enabled": False}])
        self.assertEqual(parse_config({}).clients, [])
        for items, error in (([{"name": "a b", "token": TOKEN}], "name"),
                             ([{"name": "a", "token": "short"}], "token"),
                             ([{"name": "a", "token": TOKEN}, {"name": "a", "token": TOKEN + "x"}], "уже есть"),
                             ([{"name": "a", "token": TOKEN}, {"name": "b", "token": TOKEN}], "другого клиента"),
                             ([{"name": "a", "token": TOKEN, "url": "x"}], "url: неизвестное поле")):
            with self.subTest(items=items), self.assertRaisesRegex(ConfigError, error):
                parse_config({"clients": items})

    def test_clients_url(self):
        cfg = parse_config({"pipeline": {"clients": {"url": "https://router.example:8443/"}}})
        self.assertEqual((cfg.pipeline.clients.url, cfg.pipeline.clients.output),
                         ("https://router.example:8443/", "/etc/sing-box-clients"))
        with self.assertRaisesRegex(ConfigError, "pipeline.clients.url"):
            parse_config({"pipeline": {"clients": {"url": "https://router.example:8443"}}})


class ClientsCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.out = os.path.join(self.tmp, "out")
        self.config = os.path.join(self.tmp, "config.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"clients": [{"name": "a", "token": TOKEN}, {"name": "b", "token": TOKEN + "b"},
                                   {"name": "off", "token": TOKEN + "c", "enabled": False}],
                       "pipeline": {"clients": {"output": self.out}}}, fh)

    def _cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = clients.main(["-c", self.config, *args])
        return code, out.getvalue()

    def test_list_and_output(self):
        self.assertEqual(self._cli("list"), (0, f"a {TOKEN}\nb {TOKEN}b\n"))
        self.assertEqual(self._cli("output"), (0, self.out + "\n"))
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"clients": [{"name": "a"}]}, fh)
        self.assertEqual(self._cli("list")[0], 1)

    def test_state(self):
        from nodes_tester.config import load_config
        singbox = os.path.join(self.tmp, "singbox")
        os.makedirs(os.path.join(singbox, "clients", "publish"))
        os.makedirs(os.path.join(self.out, clients.TOKEN_DIR))
        open(os.path.join(singbox, "clients", "base_a.json"), "w").close()
        open(os.path.join(singbox, "clients", "publish", "b.json"), "w").close()
        open(os.path.join(self.out, clients.TOKEN_DIR, TOKEN + ".json"), "w").close()
        state = clients.state(load_config(self.config), singbox)
        self.assertEqual([(s["name"], s["enabled"], s["source"], s["published"] is not None) for s in state],
                         [("a", True, "base", True), ("b", True, "file", False), ("off", False, None, False)])
        self.assertNotIn(TOKEN, json.dumps(state))


if __name__ == "__main__":
    unittest.main()
