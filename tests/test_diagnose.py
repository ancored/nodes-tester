import os
import tempfile
import time
import unittest

from nodes_tester.diagnose import CoreErrors, cause, core_cause

SOCKS_EOF = ("SOCKSHTTPSConnectionPool(host='curlmyip.ru', port=443): Max retries exceeded with url: /geo "
             "(Caused by SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred')))")


class CoreCauseTest(unittest.TestCase):
    def test_sing_box_errors(self):
        cases = {
            "lookup 234604.aib2bsas.online: empty result": "dns",
            "x509: certificate is valid for gamestart.ru, not gg.ru": "tls",
            "reality verification failed": "tls",
            "dial tcp 1.2.3.4:443: i/o timeout": "tcp",
            "dial tcp 1.2.3.4:443: connect: connection refused": "tcp",
            "dial tcp [2604:86c0::4]:8443: connect: network is unreachable": "tcp",
            "unexpected HTTP response status: 502": "transport",
            "context deadline exceeded": "handshake",
            "read tcp 1.2.3.4:5->6.7.8.9:443: i/o timeout": "freeze",
            "read tcp 1.2.3.4:5->6.7.8.9:443: read: connection reset by peer": "reset",
            "EOF": "reset",
        }
        for error, code in cases.items():
            with self.subTest(error=error):
                self.assertEqual(core_cause(error), code)

    def test_canceled_says_nothing(self):
        self.assertIsNone(core_cause("context canceled"))
        self.assertIsNone(core_cause(""))


class CauseTest(unittest.TestCase):
    def test_core_error_wins_over_client_error(self):
        self.assertEqual(cause("connectivity", {"core_error": "dial tcp 1.2.3.4:443: i/o timeout"}, SOCKS_EOF), "tcp")

    def test_client_error_without_core(self):
        self.assertEqual(cause("connectivity", {}, SOCKS_EOF), "reset")
        self.assertEqual(cause("download", {}, "('Connection broken: IncompleteRead(1833782 bytes read)')"), "freeze")
        self.assertEqual(cause("latency", {}, "HTTPSConnectionPool(...): Read timed out. (read timeout=10)"), "timeout")

    def test_target_and_service_before_core(self):
        core = {"core_error": "EOF"}
        self.assertEqual(cause("latency", core, "404 Client Error: Not Found for url: https://x/0MB"), "target")
        self.assertEqual(cause("download", {}, "HTTP 503"), "target")
        self.assertEqual(cause("gemini", core, "Google видит страну RUS"), "service")
        self.assertEqual(cause("openai", {}, "сервис недоступен в регионе"), "service")
        self.assertEqual(cause("gemini", {}, "HTTP 429, 429, 429"), "service")
        self.assertEqual(cause("heavy_download", core, "1.27 Мбит/с ниже порога 5"), "slow")

    def test_whitelist_has_no_cause(self):
        self.assertIsNone(cause("whitelist", {"ip_ok": False}, ""))


LINE = ("+0000 {stamp} ERROR [{cid} {age}] connection: open connection to x.com:443 "
        "using outbound/selector[{sel}]: {error}\n")


class CoreErrorsTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.remove, self.path)

    def write(self, sel, error, age="0.2s"):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(LINE.format(stamp=stamp, cid=1, age=age, sel=sel, error=error))

    def test_most_precise_error_of_the_tester_selector(self):
        self.write("nodes-tester", "EOF")            # до отметки — не берётся
        core = CoreErrors(self.path, "nodes-tester")
        mark = core.mark()
        self.write("eu-auto-out", "lookup a.example: empty result")
        self.write("nodes-tester", "context deadline exceeded")
        self.write("nodes-tester", "dial tcp 1.2.3.4:443: i/o timeout")
        self.write("nodes-tester", "context canceled")
        self.assertEqual(core.since(mark), "dial tcp 1.2.3.4:443: i/o timeout")

    def test_connection_started_before_the_test_is_skipped(self):
        core = CoreErrors(self.path, "nodes-tester")
        mark = core.mark()
        self.write("nodes-tester", "EOF", age="30.0s")
        self.assertEqual(core.since(mark), "")

    def test_truncated_log_is_read_from_start(self):
        for _ in range(3):
            self.write("nodes-tester", "EOF")
        core = CoreErrors(self.path, "nodes-tester")
        mark = core.mark()
        open(self.path, "w").close()
        self.write("nodes-tester", "reality verification failed")
        self.assertEqual(core.since(mark), "reality verification failed")

    def test_no_log(self):
        core = CoreErrors("", "nodes-tester")
        self.assertEqual(core.since(core.mark()), "")


class ProviderActivityTest(unittest.TestCase):
    def test_share_activations_and_emergencies(self):
        from dashboard.data import _provider_activity
        since, now = 1000, 2000
        acts = [  # до окна группа уже стояла на ноде A
            {"region": "eu", "tag": "A-vless-out [1]", "reason": "rotation", "ts": 500},
            {"region": "eu", "tag": "B-vless-out [2]", "reason": "emergency", "ts": 1400},
            {"region": "eu", "tag": "A-vless-out [3]", "reason": "rotation", "ts": 1800},
            {"region": "us", "tag": "B-vless-out [4]", "reason": "rotation", "ts": 1200},
        ]
        activity, active = _provider_activity("A", acts, since, now)
        self.assertEqual(activity, [{"group": "eu", "share": 0.6, "seconds": 600,
                                     "activations": 1, "emergencies": 1}])
        self.assertEqual(active, ["eu"])


if __name__ == "__main__":
    unittest.main()
