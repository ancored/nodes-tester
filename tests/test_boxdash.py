"""Dashboard sing-box через админку: прокси статики и выдача сервера администратору."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from tests.helpers import make_config, temp_dir
from nodes_tester.config import load_config
from dashboard.server import build_app


class _Resp:
    def __init__(self, body, ctype):
        self.body, self.headers = body, {"Content-Type": ctype}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BoxDashboardTest(unittest.TestCase):
    def setUp(self):
        tmp = temp_dir()
        self.cfg = load_config(make_config(tmp, dashboard={"enabled": True, "token": "secret"}))
        self.app = build_app(self.cfg)

    def test_proxies_dashboard_files(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b"<html>", "text/html")) as op:
            r = self.app.handle("GET", "/sing-box-dashboard/", {}, b"")
            self.assertEqual((r.status, r.body), (200, b"<html>"))
            self.app.handle("GET", "/sing-box-dashboard/assets/a.js", {}, b"")
        base = self.cfg.box_api.url.rstrip("/")
        self.assertEqual([c.args[0] for c in op.call_args_list],
                         [f"{base}/dashboard/", f"{base}/dashboard/assets/a.js"])

    def test_rejects_traversal(self):
        with mock.patch("urllib.request.urlopen") as op:
            r = self.app.handle("GET", "/sing-box-dashboard/../../etc/passwd", {}, b"")
        self.assertIn(r.status, (404, 200))
        for c in op.call_args_list:
            self.assertIn("/dashboard/", c.args[0])
            self.assertNotIn("..", c.args[0])

    def test_server_secret_needs_token(self):
        self.assertEqual(self.app.handle("GET", "/api/singbox/dashboard", {}, b"").status, 401)
        r = self.app.handle("GET", "/api/singbox/dashboard", {"X-Admin-Token": "secret"}, b"")
        body = json.loads(r.body)
        self.assertEqual(body["url"], self.cfg.box_api.url)
        self.assertEqual(body["secret"], self.cfg.box_api.secret or "")


if __name__ == "__main__":
    unittest.main()
