"""Уведомления: фильтр событий, подавление повторов, разбор прогонов и подписок."""

import time
import unittest
from types import SimpleNamespace

from nodes_tester.config import NotifyConfig, TelegramConfig, WebhookConfig
from nodes_tester.notify import Notifier


def _cfg(**kw):
    cfg = NotifyConfig(enabled=True, telegram=TelegramConfig("123:ABC", "42"),
                       webhook=WebhookConfig("https://hook.invalid/x"), **kw)
    return cfg


class _Session:
    def __init__(self, status=200):
        self.posts = []
        self.status = status

    def post(self, url, json=None, timeout=None, proxies=None):
        self.posts.append((url, json))
        return SimpleNamespace(status_code=self.status, text="err")


class _Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.n = Notifier(_cfg(), clock=self.clock)
        self.sent = []
        # Очередь не нужна в тестах: перехватываем постановку.
        self.n._queue.put_nowait = lambda item: self.sent.append(item)
        self.n._ensure_worker = lambda: None

    def texts(self):
        return [t for _e, t in self.sent]

    def test_disabled_without_recipients(self):
        n = Notifier(NotifyConfig(enabled=True))
        self.assertFalse(n.enabled)
        self.assertFalse(n.send("switch", "x"))

    def test_switch_reasons_filter(self):
        self.n.activation("eu", "rotation", "A", "B")
        self.n.activation("eu", "emergency", "A", "B")
        self.assertEqual(self.texts(), ["Группа eu: авария активной ноды → A (была B)"])

    def test_failsafe_and_dedupe(self):
        self.n.activation("ai", "failsafe", "ai-auto-out-failsafe", None)
        self.n.activation("ai", "failsafe", "ai-auto-out-failsafe", None)
        self.assertEqual(len(self.sent), 1)
        self.clock.t += 3601
        self.n.activation("ai", "failsafe", "ai-auto-out-failsafe", None)
        self.assertEqual(len(self.sent), 2)

    def test_event_filter_and_rate_limit(self):
        n = Notifier(_cfg(events=["rollback"], max_per_hour=2), clock=self.clock)
        n._queue.put_nowait = lambda item: self.sent.append(item)
        n._ensure_worker = lambda: None
        self.assertFalse(n.send("switch", "x"))
        for i in range(3):
            n.send("rollback", f"r{i}")
        self.assertEqual(self.texts(), ["r0", "r1"])

    def test_pipeline_rollback_and_error(self):
        log = ("[apply] 2026-10-01 ОШИБКА: откатились на прежний config.json, связность есть\n")
        self.n.pipeline_finished({"mode": "router", "dry_run": 0, "status": "error"}, log)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("вернули прежний", self.texts()[0])
        self.assertIn("«роутер» завершился: ошибка", self.texts()[1])

    def test_dry_run_is_silent(self):
        self.n.pipeline_finished({"mode": "router", "dry_run": 1, "status": "error"}, "")
        self.assertEqual(self.sent, [])

    def test_subscription_failure_and_expiry(self):
        now = self.clock.t
        last_ok = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 30 * 3600))
        self.n.subscriptions_state([
            {"provider": "MEDVED", "ok": False, "stale": True, "count": 66,
             "last_ok_at": last_ok, "error": "HTTPError: 502"},
            {"provider": "LUNA", "ok": True, "meta": {"expire": int(now + 2 * 86400),
                                                      "total": 100, "download": 95}},
            {"provider": "FAR", "ok": True, "meta": {"expire": int(now + 30 * 86400)}},
        ], stale_max_hours=48)
        texts = self.texts()
        self.assertEqual(len(texts), 3)
        self.assertIn("работают прошлые ноды (66), пропадут через ~18 ч", texts[0])
        self.assertIn("LUNA заканчивается", texts[1])
        self.assertIn("израсходовано 95%", texts[2])
        # Срок подписки — не чаще раза в сутки, даже при прогоне каждые 3 ч.
        self.clock.t += 3 * 3600
        self.n.subscriptions_state([{"provider": "LUNA", "ok": True,
                                     "meta": {"expire": int(now + 2 * 86400)}}])
        self.assertEqual(len(self.sent), 3)

    def test_deliver_masks_token_and_reports(self):
        session = _Session(status=500)
        self.assertFalse(self.n.deliver(session, "test", "hi"))
        self.assertNotIn("123:ABC", self.n.last_error or "")
        self.assertEqual([u.split("/bot")[0] for u, _ in session.posts][:1], ["https://api.telegram.org"])
        self.assertEqual(session.posts[1][1]["event"], "test")


    def test_fallback_through_tester_when_direct_fails(self):
        used = []

        def fallback(fn):
            used.append(1)
            return fn(_Session(status=200))
        n = Notifier(_cfg(), clock=self.clock, fallback=fallback)
        self.assertTrue(n.deliver(_Session(status=500), "test", "hi"))   # напрямую 500 → через тестер
        self.assertEqual(len(used), 2)                                    # Telegram и webhook
        self.assertIsNone(n.last_error)

    def test_no_fallback_when_direct_works(self):
        n = Notifier(_cfg(), clock=self.clock, fallback=lambda fn: self.fail("не нужен"))
        self.assertTrue(n.deliver(_Session(status=200), "test", "hi"))


if __name__ == "__main__":
    unittest.main()
