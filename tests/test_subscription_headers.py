"""Заголовки подписок: HWID установки, метаданные ответа панели, повторы при 5xx."""

import base64
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from nodes_fetch import device, fetch, happ, meta, util
from nodes_fetch.sources import Context, fetch_subscription
from tests.helpers import temp_dir

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LINK = "vless://11111111-2222-3333-4444-555555555555@example.invalid:443?security=tls#demo"
INFO = {"Subscription-Userinfo": "upload=1; download=2; total=30; expire=1792577243",
        "Profile-Title": "base64:" + base64.b64encode("Demo VPN".encode()).decode(),
        "Announce": "Продлите подписку", "Profile-Update-Interval": "12",
        "Support-Url": "https://t.me/support"}


class MetaTest(unittest.TestCase):
    def test_parses_panel_headers(self):
        self.assertEqual(meta.from_headers(INFO), {
            "upload": 1, "download": 2, "total": 30, "expire": 1792577243,
            "title": "Demo VPN", "announce": "Продлите подписку",
            "support_url": "https://t.me/support", "update_interval_hours": 12.0})

    def test_case_insensitive_and_garbage(self):
        self.assertEqual(meta.from_headers({"subscription-userinfo": "total=x; expire=0"}),
                         {"expire": 0})
        self.assertEqual(meta.from_headers({"Profile-Title": "base64:!!!"}), {})
        self.assertEqual(meta.from_headers(None), {})


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()

    def test_new_install_gets_random_persistent_hwid(self):
        path = os.path.join(self.tmp, "device.json")
        first = device.load(path, log=lambda m: None)["hwid"]
        self.assertEqual(len(first), 16)
        self.assertEqual(device.load(path, log=lambda m: None)["hwid"], first)

    def test_default_path_shared_by_sets(self):
        main = os.path.join(self.tmp, "config-main", "providers.json")
        wh = os.path.join(self.tmp, "config-wh", "providers.json")
        self.assertEqual(device.default_path(main), device.default_path(wh))
        self.assertEqual(device.default_path(main), os.path.join(self.tmp, "device.json"))


def _resp(status, body=LINK, headers=None):
    r = Mock(status_code=status, content=body.encode(), headers=headers or {})
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(response=Mock(status_code=status))
    return r


class HappTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        patcher = patch("nodes_fetch.happ.resolve_url",
                        return_value="https://panel.invalid/sub")
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = patch("nodes_fetch.happ.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_hwid_from_device_and_meta_from_response(self):
        ctx = Context(self.tmp, device={"hwid": "own"})
        with patch("nodes_fetch.happ.requests.get", return_value=_resp(200, headers=INFO)) as get:
            nodes = fetch_subscription({"tag": "H", "url": "happ://crypt5/x"}, ctx)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(get.call_args.kwargs["headers"]["X-Hwid"], "own")
        self.assertEqual(meta.from_headers(ctx.response_headers)["total"], 30)

    def test_subscription_override_wins(self):
        ctx = Context(self.tmp, device={"hwid": "own"})
        sub = {"tag": "H", "url": "happ://crypt5/x", "happ_headers": {"X-Hwid": "manual"}}
        with patch("nodes_fetch.happ.requests.get", return_value=_resp(200)) as get:
            fetch_subscription(sub, ctx)
        self.assertEqual(get.call_args.kwargs["headers"]["X-Hwid"], "manual")

    def test_502_is_retried(self):
        with patch("nodes_fetch.happ.requests.get",
                   side_effect=[_resp(502), _resp(502), _resp(200)]) as get:
            text = happ.subscription_text("happ://crypt5/x")
        self.assertIn("vless://", text)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 10])

    def test_4xx_not_retried(self):
        with patch("nodes_fetch.happ.requests.get", return_value=_resp(403)) as get, \
                self.assertRaises(requests.HTTPError):
            happ.subscription_text("happ://crypt5/x")
        self.assertEqual(get.call_count, 1)


class UrlDeviceAndMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.calls = []
        old = util.http_get
        self.addCleanup(setattr, util, "http_get", old)

        def fake(url, user_agent=None, timeout=None, proxies=None, headers=None):
            self.calls.append(headers)
            if self.fail:
                return None
            return Mock(status_code=200, content=LINK.encode(), text=LINK, headers=INFO)
        util.http_get = fake
        self.fail = False

    def _run(self, sub, previous=None):
        norm = fetch.load_providers({"subscribes": [sub], "fetch": {"retries": 0}},
                                    log=lambda m: None)
        return fetch.run(norm, self.tmp, previous=previous, now=NOW, log=lambda m: None,
                         device={"hwid": "own"})

    def test_device_headers_only_when_opted_in(self):
        self._run({"tag": "A", "url": "https://s.invalid/a"})
        self._run({"tag": "A", "url": "https://s.invalid/a", "send_device": True})
        self.assertIsNone(self.calls[0])
        self.assertEqual(self.calls[1]["X-Hwid"], "own")

    def test_meta_saved_and_kept_on_failure(self):
        raw = self._run({"tag": "A", "url": "https://s.invalid/a"})
        self.assertEqual(raw["sources"][0]["meta"]["title"], "Demo VPN")
        self.fail = True
        again = self._run({"tag": "A", "url": "https://s.invalid/a"}, previous=raw)
        self.assertFalse(again["sources"][0]["ok"])
        self.assertEqual(again["sources"][0]["meta"], raw["sources"][0]["meta"])
        self.assertEqual(raw["nodes_hash"], again["nodes_hash"])   # meta не влияет на ноды

    def test_send_device_must_be_bool(self):
        with self.assertRaises(fetch.ProvidersError):
            fetch.load_providers({"subscribes": [{"tag": "A", "url": "https://x",
                                                  "send_device": "yes"}]})


if __name__ == "__main__":
    unittest.main()
