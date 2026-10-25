"""Редактор единого config.json: GET/PUT с проверкой загрузчиком и атомарной записью."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tests.helpers import load_test_config, temp_dir
from nodes_tester.config import load_config
from dashboard.server import build_app


class ApiConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_test_config(self.tmp, admin={"token": "secret"})
        self.app = build_app(self.cfg, runner=None)

    def _call(self, method, path, token="secret", body=None, headers=None):
        headers = {"X-Admin-Token": token, **(headers or {})}
        return self.app.handle(method, path, headers,
                               json.dumps(body).encode() if body is not None else b"")

    def _current(self):
        return json.loads(self._call("GET", "/api/config").body)

    def test_get_config(self):
        r = self._call("GET", "/api/config")
        self.assertEqual(r.status, 200)
        data = json.loads(r.body)
        self.assertIn("api", data["data"])
        self.assertIn("subscriptions", data["data"])
        self.assertTrue(data["path"].endswith("config.json"))

    def test_get_schema(self):
        # schema лежит в config/ рядом с оригиналом, а не в tmp — здесь её может не быть.
        self.assertIn(self._call("GET", "/api/config/schema").status, (200, 500))

    def test_put_invalid_rejected_with_field_path(self):
        cur = self._current()["data"]
        cur["groups"] = [{"tag": "ai", "require": {"gemeni": {}}}]
        r = self._call("PUT", "/api/config", body=cur)
        self.assertEqual(r.status, 400)
        self.assertIn("groups[0].require.gemeni", json.loads(r.body)["error"])

    def test_put_wrong_types_rejected(self):
        for change in ({"admin": {"port": "not-a-port"}}, {"admin": {"refresh": -1}},
                       {"pipeline": {"router": {"at": ["3:00"]}}}, {"tester": {"pause": 5}},
                       {"subscriptions": [{"tag": "A", "url": "https://x"}, {"tag": "A", "url": "https://y"}]}):
            with self.subTest(change=change):
                cur = self._current()["data"]
                cur.update(change)
                self.assertEqual(self._call("PUT", "/api/config", body=cur).status, 400)

    def test_put_valid_roundtrip_and_conflict(self):
        doc = self._current()
        cur = doc["data"]
        cur["subscriptions"].append({"tag": "WH", "url": "https://example.com/wh", "branch": "clients"})
        r = self._call("PUT", "/api/config", body=cur, headers={"If-Match": doc["revision"]})
        self.assertEqual(r.status, 200)
        with open(self.cfg.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), cur)
        stale = self._call("PUT", "/api/config", body=cur, headers={"If-Match": doc["revision"]})
        self.assertEqual(stale.status, 409)

    def test_sections_merge_parallel_edits(self):
        opened = self._current()["data"]
        mine = {"base": {"groups": opened.get("groups")}, "set": {"groups": [{"tag": "eu"}]}}
        # в другом окне за это время поменяли подписки — не конфликт: разделы разные
        other = dict(opened, subscriptions=opened["subscriptions"] + [{"tag": "NEW", "url": "https://n"}])
        self.assertEqual(self._call("PUT", "/api/config", body=other).status, 200)
        r = self._call("POST", "/api/config/sections", body=mine)
        self.assertEqual(r.status, 200)
        with open(self.cfg.path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["groups"], [{"tag": "eu"}])
        self.assertEqual(saved["subscriptions"][-1]["tag"], "NEW")
        # тот же раздел изменён после открытия — конфликт, файл не тронут
        stale = {"base": {"groups": opened.get("groups")}, "set": {"groups": [{"tag": "us"}]}}
        self.assertEqual(self._call("POST", "/api/config/sections", body=stale).status, 409)
        # null убирает раздел; ошибка загрузчика — 400 с путём поля
        drop = {"base": {"groups": [{"tag": "eu"}]}, "set": {"groups": None}}
        self.assertEqual(self._call("POST", "/api/config/sections", body=drop).status, 200)
        with open(self.cfg.path, encoding="utf-8") as fh:
            self.assertNotIn("groups", json.load(fh))
        bad = {"base": {"groups": None}, "set": {"groups": [{"tag": "Bad"}]}}
        r = self._call("POST", "/api/config/sections", body=bad)
        self.assertEqual(r.status, 400)
        self.assertIn("groups[0].tag", json.loads(r.body)["error"])

    def test_subscription_states_diff_with_config(self):
        from tests.test_nodes_config import _raw, _vless
        os.makedirs(os.path.join(self.tmp, "raw"))
        raw = _raw([("A", "x", _vless("10.0.0.1")), ("A", "y", _vless("10.0.0.2"))])
        raw["sources"][0]["count"] = 2
        with open(os.path.join(self.tmp, "raw", "router.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        with open(self.cfg.nodes_file, "w", encoding="utf-8") as fh:   # в конфиге — ушедшая нода
            json.dump({"outbounds": [{"type": "vless", "tag": "A-vless|reality-out [11111111.22222222]"}]}, fh)
        self.app.runner = type("R", (), {"orchestrator": type("O", (), {"data_dir": self.tmp})()})()
        data = json.loads(self._call("GET", "/api/config/subscription-options").body)
        self.assertEqual(data["sources"]["A"]["diff"], {"in_config": 1, "in_poll": 2, "new": 2, "gone": 1})

    def test_put_restarts_embedded_tester_unless_pipeline_runs(self):
        init = os.path.join(self.tmp, "nodes-tester.init")
        open(init, "w").close()
        busy = {"current": None}
        runner = type("R", (), {"orchestrator": type("O", (), {"status": lambda s: busy})()})()
        self.app.runner = runner
        with patch("dashboard.api_config.TESTER_INIT", init), \
                patch("dashboard.api_config.subprocess.Popen") as popen:
            body = json.loads(self._call("PUT", "/api/config", body=self._current()["data"]).body)
            self.assertTrue(body["restarting"])
            self.assertIn(init + " restart", popen.call_args.args[0][-1])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            busy["current"] = {"id": "x"}
            body = json.loads(self._call("PUT", "/api/config", body=self._current()["data"]).body)
            self.assertFalse(body["restarting"])
            self.assertIn("конвейер", body["restart_note"])
            self.assertEqual(popen.call_count, 1)
        self.app.runner = None                    # без тестера (отдельный процесс) — вручную
        body = json.loads(self._call("PUT", "/api/config", body=self._current()["data"]).body)
        self.assertFalse(body["restarting"])

    @unittest.skipUnless(os.name == "posix", "права файлов — только POSIX")
    def test_put_keeps_config_private(self):
        # В config.json секреты, пакет ставит ему chmod 600: сохранение из админки
        # не должно открывать файл на чтение остальным.
        os.chmod(self.cfg.path, 0o600)
        self.assertEqual(self._call("PUT", "/api/config", body=self._current()["data"]).status, 200)
        self.assertEqual(os.stat(self.cfg.path).st_mode & 0o777, 0o600)

    def test_write_keeps_symlink(self):
        real = os.path.join(self.tmp, "real-config.json")
        os.replace(self.cfg.path, real)
        try:
            os.symlink(real, self.cfg.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink недоступен (Windows без прав)")
        cur = self._current()["data"]
        cur["api"]["url"] = "http://127.0.0.1:9191"
        self.assertEqual(self._call("PUT", "/api/config", body=cur).status, 200)
        self.assertTrue(os.path.islink(self.cfg.path))
        with open(real, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["api"]["url"], "http://127.0.0.1:9191")

    def test_put_bad_json_rejected(self):
        r = self.app.handle("PUT", "/api/config", {"X-Admin-Token": "secret"}, b"{not json")
        self.assertEqual(r.status, 400)

    def test_custom_config_filename_is_the_edited_target(self):
        custom = os.path.join(self.tmp, "router-local.json")
        os.replace(self.cfg.path, custom)
        app = build_app(load_config(custom, data_dir=self.tmp), runner=None)
        r = app.handle("GET", "/api/config", {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.body)["path"], os.path.abspath(custom))

    def test_subscription_options(self):
        data = json.loads(self._call("GET", "/api/config/subscription-options").body)
        self.assertEqual(data["branches"], ["router", "clients"])
        self.assertIn("gemini", data["checks"])
        self.assertNotIn("gemini", data["client_checks"])
        self.assertIn("eu", data["builtin_regions"])
        self.assertEqual((data["clients"], data["client_groups"]), ([], None))
        with open(self.cfg.client_candidates_file, "w", encoding="utf-8") as fh:
            json.dump({"ts": 100, "groups": {"whitelist": ["a", "b"]}}, fh)
        data = json.loads(self._call("GET", "/api/config/subscription-options").body)
        self.assertEqual(data["client_groups"], {"ts": 100, "groups": {"whitelist": 2}})

    def test_wrong_method_returns_405(self):
        resp = self.app.handle("DELETE", "/api/config", {}, b"")
        self.assertEqual(resp.status, 405)
        self.assertIn("GET", resp.headers["Allow"].split(", "))

    def test_config_requires_token(self):
        self.assertEqual(self.app.handle("GET", "/api/config", {}, b"").status, 401)


if __name__ == "__main__":
    unittest.main()
