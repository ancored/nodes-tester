"""Admin refinement: real temp files/SQLite, no external requests or selectors."""
import json
import os
import unittest
from unittest.mock import patch, Mock

from dashboard.server import build_app
from naming import canonical, node_key, split_payload
from nodes_fetch.fetch import load_providers
from nodes_fetch.sources import Context, fetch_subscription
from nodes_fetch.happ import _HEADERS
from tests.helpers import temp_dir, make_runner, fresh_nodes_json


class AdminRefinementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.runner = make_runner(self.tmp, admin={"token": "demo"})
        self.app = build_app(self.runner.cfg, runner=self.runner)

    def tearDown(self):
        self.runner.storage.close()

    def call(self, method, path, body=None, revision=None, token="demo"):
        headers = {"X-Admin-Token": token}
        if revision is not None:
            headers["If-Match"] = revision
        return self.app.handle(method, path, headers, json.dumps(body).encode() if body is not None else b"")

    def test_config_and_options_require_token(self):
        for endpoint in ("/api/config", "/api/config/subscription-options"):
            self.assertEqual(self.call("GET", endpoint, token="").status, 401)
        data = json.loads(self.call("GET", "/api/config/subscription-options").body)
        self.assertEqual(data["happ_headers"], _HEADERS)
        self.assertEqual([o["value"] for o in data["user_agents"]], ["", "curl", "clashmeta"])
        self.assertEqual(data["branches"], ["router", "clients"])

    def load_node(self):
        ob = {"type": "vless", "server": "example.invalid", "server_port": 443, "uuid": "demo-secret",
              "tls": {"enabled": True, "_nested": "included"}, "domain_resolver": "bootstrap", "_temporary": True}
        crc = node_key(ob)
        ob["tag"] = f"DEMO-vless-out [{crc}]"
        path = fresh_nodes_json(self.tmp, [ob])
        self.runner.cfg.storage.nodes_file = path
        self.runner.storage.load_nodes(path)
        db = self.runner.storage._db
        db.executemany("INSERT INTO score_history(ts,crc,score) VALUES (?,?,?)", [(10, crc, 0), (20, crc, 81), (20, crc, 82)])
        db.commit()
        return ob, crc, path

    def test_card_has_exact_fragment_crc_and_all_dated_scores_without_public_secrets(self):
        ob, crc, path = self.load_node()
        data = json.loads(self.call("GET", f"/api/nodes/{crc}/details").body)
        self.assertEqual(data["fragment"], ob)
        settings, creds = split_payload(ob)
        self.assertEqual(data["node_payload"], canonical(settings))
        self.assertEqual(data["cred_payload"], canonical(creds))
        self.assertEqual(json.loads(data["cred_payload"]), {"uuid": "demo-secret"})
        self.assertEqual(data["cred_paths"], [["uuid"]])
        self.assertEqual(data["calculated_crc"], crc)
        self.assertNotIn("tag", data["crc_fields"])
        self.assertNotIn("domain_resolver", data["crc_fields"])
        self.assertNotIn("_temporary", data["crc_fields"])
        self.assertIn("tls", data["crc_fields"])
        self.assertEqual(data["score_history"], [{"sample_id": 3, "ts": 20, "score": 82}, {"sample_id": 2, "ts": 20, "score": 81}, {"sample_id": 1, "ts": 10, "score": 0}])
        self.assertEqual(self.call("GET", f"/api/nodes/{crc}/details", token="").status, 401)
        public = self.call("GET", f"/api/nodes/{crc}/score-history", token="")
        self.assertEqual(public.status, 200)
        self.assertNotIn(b"demo-secret", public.body)
        self.assertNotIn(b"demo-secret", self.call("GET", "/api/data", token="").body)
        os.unlink(path)
        fallback = json.loads(self.call("GET", f"/api/nodes/{crc}/details").body)
        self.assertEqual(fallback["fragment_source"], "sqlite")
        self.assertEqual(fallback["calculated_crc"], crc)
        self.assertNotIn("domain_resolver", fallback["fragment"])

    def test_node_routes_validate_crc_and_missing_node(self):
        self.assertEqual(self.call("GET", "/api/nodes/nope/details").status, 400)
        self.assertEqual(self.call("GET", "/api/nodes/12345678.9abcdef0/details").status, 404)

    def test_happ_headers_override_and_url_user_agent_is_ignored(self):
        sub = {"tag": "HAPP", "url": "happ://crypt/demo", "user_agent": "ignored",
               "happ_headers": {"User-Agent": "Happ/demo", "X-Hwid": "own-device"}}
        response = Mock(content=b"vless://123@example.invalid:443#demo", headers={})
        with patch("nodes_fetch.happ.resolve_url", return_value="https://example.invalid/sub"), \
             patch("nodes_fetch.happ.requests.get", return_value=response) as get:
            self.assertTrue(fetch_subscription(sub, Context(self.tmp)))
        sent = get.call_args.kwargs["headers"]
        self.assertEqual(sent["User-Agent"], "Happ/demo")
        self.assertEqual(sent["X-Hwid"], "own-device")
        self.assertEqual(sent["X-Device-Os"], _HEADERS["X-Device-Os"])

    def test_bad_headers_are_rejected_before_fetch(self):
        for headers in ([], {"X-Hwid": 3}, {"": "x"}, {"X-Test": "value\r\nInjected: yes"}):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                load_providers({"subscribes": [{"tag": "A", "url": "happ://crypt/demo", "happ_headers": headers}]})
