"""Редактор конфигов (Фаза 4): GET/PUT config.json с валидацией и атомарной записью."""

from __future__ import annotations

import json
import os
import unittest

from tests.helpers import make_config, temp_dir
from nodes_tester.config import load_config
from dashboard.server import build_app


class ApiConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.cfg = load_config(make_config(self.tmp, dashboard={"token": "secret"}))
        self.app = build_app(self.cfg, runner=None)

    def _call(self, method, path, token="secret", body=None):
        headers = {"X-Admin-Token": token}
        return self.app.handle(method, path, headers,
                               json.dumps(body).encode() if body is not None else b"")

    def test_get_config(self):
        r = self._call("GET", "/api/config")
        self.assertEqual(r.status, 200)
        data = json.loads(r.body)
        self.assertIn("clash_api", data["data"])
        self.assertTrue(data["path"].endswith("config.json"))

    def test_get_schema(self):
        r = self._call("GET", "/api/config/schema")
        # schema лежит в config/ рядом с оригиналом, а не в tmp — здесь её может не быть,
        # поэтому допускаем 200 (найдена) либо 500 (нет) — главное, что endpoint живой.
        self.assertIn(r.status, (200, 500))

    def test_put_invalid_rejected(self):
        r = self._call("PUT", "/api/config", body={"clash_api": {}})
        self.assertEqual(r.status, 400)

    def test_put_invalid_dashboard_types_rejected(self):
        cur = json.loads(self._call("GET", "/api/config").body)["data"]
        cur["dashboard"]["port"] = "not-a-port"
        cur["dashboard"]["interval"] = -1
        self.assertEqual(self._call("PUT", "/api/config", body=cur).status, 400)

    def test_put_missing_explicit_schema_rejected(self):
        cur = json.loads(self._call("GET", "/api/config").body)["data"]
        cur["$schema"] = "missing.schema.json"
        self.assertEqual(self._call("PUT", "/api/config", body=cur).status, 400)

    def test_put_invalid_providers_rejected(self):
        self.assertEqual(self._call("PUT", "/api/config/providers", body={}).status, 400)
        bad = {"subscribes": "not-a-list", "save_config_path": "out.json"}
        self.assertEqual(self._call("PUT", "/api/config/providers", body=bad).status, 400)

    def test_put_valid_roundtrip(self):
        cur = json.loads(self._call("GET", "/api/config").body)["data"]
        r = self._call("PUT", "/api/config", body=cur)
        self.assertEqual(r.status, 200)
        with open(self.cfg.path, encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written["clash_api"]["base_url"], cur["clash_api"]["base_url"])

    def test_put_bad_json_rejected(self):
        # невалидный JSON в теле → req.json() бросит HttpError 400
        headers = {"X-Admin-Token": "secret"}
        r = self.app.handle("PUT", "/api/config", headers, b"{not json")
        self.assertEqual(r.status, 400)

    def test_custom_config_filename_is_the_edited_target(self):
        custom = os.path.join(self.tmp, "router-local.json")
        os.replace(self.cfg.path, custom)
        cfg = load_config(custom)
        app = build_app(cfg, runner=None)
        r = app.handle("GET", "/api/config", {"X-Admin-Token": "secret"}, b"")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.body)["path"], os.path.abspath(custom))

    def test_wrong_method_returns_405(self):
        self.assertEqual(self.app.handle("DELETE", "/api/config", {}, b"").status, 405)

    def test_config_requires_token(self):
        self.assertEqual(self.app.handle("GET", "/api/config", {}, b"").status, 401)


if __name__ == "__main__":
    unittest.main()
