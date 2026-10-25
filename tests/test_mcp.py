"""MCP-эндпоинт админки: протокол JSON-RPC поверх /mcp и инструменты на фейковом Runner."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest import mock

from tests.helpers import fresh_nodes_json, load_test_config, temp_dir
from tests.test_api_control import FakeRunner
from nodes_tester.storage import Storage
from dashboard import mcp
from dashboard.mcp import TOOLS
from dashboard.server import build_app

TAG = "LUNA-vless|reality-out [abcd1234.0000beef]"
KEY = "abcd1234.0000beef"


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.box_config = os.path.join(self.tmp, "sing-box.json")
        self.box_log = os.path.join(self.tmp, "sing-box.log")
        with open(self.box_config, "w", encoding="utf-8") as fh:
            json.dump({"log": {"output": self.box_log}}, fh)
        self.cfg = load_test_config(self.tmp, admin={"enabled": True, "token": "secret"},
                                    singbox={"config": self.box_config})
        self.storage = Storage(self.cfg.storage)
        fresh_nodes_json(self.tmp, [{"tag": TAG, "type": "vless", "server": "1.2.3.4", "server_port": 443}])
        self.storage.load_nodes(self.cfg.nodes_file)
        self.storage.add_results({"id": KEY, "tests": {
            "connectivity": {"ok": True, "latency_ms": 120, "country": "NL"}}})
        self.runner = FakeRunner(self.cfg, self.storage)
        self.app = build_app(self.cfg, runner=self.runner)

    def tearDown(self):
        self.storage.close()

    def _post(self, message, headers=None):
        headers = {"Authorization": "Bearer secret", **(headers or {})}
        return self.app.handle("POST", "/mcp", headers, json.dumps(message).encode("utf-8"))

    def _rpc(self, method, params=None):
        resp = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        self.assertEqual(resp.status, 200)
        return json.loads(resp.body)

    def _tool(self, name, **arguments):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        text = result["content"][0]["text"]
        if result["isError"]:
            return text, True
        try:
            return json.loads(text), False
        except ValueError:
            return text, False

    def test_initialize_and_list(self):
        init = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.assertIn("overview", init["instructions"])
        tools = self._rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(TOOLS))
        reading = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
        self.assertIn("query_db", reading)
        self.assertNotIn("node_action", reading)

    def test_unknown_protocol_version_gets_ours(self):
        init = self._rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(init["protocolVersion"], "2025-11-25")

    def test_transport_rules(self):
        self.assertEqual(self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}).status, 202)
        self.assertEqual(self._post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"Authorization": "Bearer wrong"}).status, 401)
        self.assertEqual(self._post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"Origin": "http://evil.example", "Host": "192.168.1.1:8088"}).status, 403)
        self.assertEqual(self._post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"MCP-Protocol-Version": "1999-01-01"}).status, 400)
        get = self.app.handle("GET", "/mcp", {"Authorization": "Bearer secret"}, b"")
        self.assertEqual((get.status, get.headers.get("Allow")), (405, "POST"))
        self.assertEqual(self._rpc("no/such")["error"]["code"], -32601)
        broken = self.app.handle("POST", "/mcp", {"Authorization": "Bearer secret"}, b"{")
        self.assertEqual(json.loads(broken.body)["error"]["code"], -32700)

    def test_batch(self):
        resp = self._post([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        self.assertEqual([r["id"] for r in json.loads(resp.body)], [1, 2])
        self.assertEqual(json.loads(self._post([]).body)["error"]["code"], -32600)
        bad = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": []})
        self.assertEqual(json.loads(bad.body)["error"]["code"], -32602)

    def test_argument_checks(self):
        self.assertEqual(self._rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)
        text, error = self._tool("list_nodes", limit=0)
        self.assertTrue(error)
        self.assertIn("limit", text)
        self.assertTrue(self._tool("list_nodes", colour="red")[1])
        self.assertTrue(self._tool("node_action", node=KEY)[1])
        self.assertTrue(self._tool("node_action", node=KEY, action="explode")[1])

    def test_list_and_get_node(self):
        nodes, error = self._tool("list_nodes", search="luna")
        self.assertFalse(error)
        self.assertEqual([n["crc"] for n in nodes["nodes"]], [KEY])
        self.assertEqual(self._tool("list_nodes", provider="RURU")[0]["total"], 0)
        node, error = self._tool("get_node", node=TAG)
        self.assertFalse(error)
        self.assertEqual(node["node"]["crc"], KEY)
        self.assertNotIn("payload", node["node"])
        self.assertEqual(node["latest_results"][0]["metrics"]["latency_ms"], 120)
        self.assertIn("не найдена", self._tool("get_node", node="deadbeef.00000000")[0])

    def test_query_db_is_read_only(self):
        rows, error = self._tool("query_db", sql="SELECT crc, test FROM results WHERE crc = ?", params=[KEY])
        self.assertFalse(error)
        self.assertEqual(rows["rows"], [[KEY, "connectivity"]])
        for sql in ("DELETE FROM results", "ATTACH DATABASE 'x.db' AS x", "PRAGMA journal_mode=DELETE",
                    "SELECT 1; DELETE FROM results"):
            self.assertTrue(self._tool("query_db", sql=sql)[1], sql)
        self.assertEqual(self._tool("query_db", sql="SELECT COUNT(*) FROM results")[0]["rows"], [[1]])
        cut, _ = self._tool("query_db", sql="WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n "
                                            "WHERE i < 10) SELECT i FROM n", limit=3)
        self.assertEqual((len(cut["rows"]), cut["truncated"]), (3, True))

    def test_node_action_and_switch(self):
        self.assertFalse(self._tool("node_action", node=KEY, action="ban")[1])
        self.assertIn(KEY, self.storage.banned_crcs())
        self.assertFalse(self._tool("switch_group", group="eu", node="X")[1])
        self.assertEqual(self.runner.switcher.forced, [("eu", "X")])

    def test_logs(self):
        self.runner.log.write("измерение\nпереключение eu\n")
        text, _ = self._tool("tester_log", grep="eu")
        self.assertTrue(text.endswith("переключение eu"))
        self.assertEqual(text.count("\n"), 0)
        with open(self.box_log + ".1", "w", encoding="utf-8") as fh:
            fh.write("old ERROR a\n")
        with open(self.box_log, "w", encoding="utf-8") as fh:
            fh.write("INFO b\nERROR c\n")
        self.assertEqual(self._tool("singbox_log", grep="error")[0], "ERROR c")
        self.assertEqual(self._tool("singbox_log", grep="error", rotated=True)[0], "old ERROR a\nERROR c")

    def test_overview_survives_missing_parts(self):
        data, error = self._tool("overview")
        self.assertFalse(error)
        self.assertTrue(data["tester"]["running"])
        self.assertIn("core", data)
        self.assertIn("error", data["pipeline"])         # у фейкового Runner нет конвейера

    def test_config_round_trip(self):
        doc, _ = self._tool("get_config", sections=["admin"])
        self.assertEqual(list(doc["data"]), ["admin"])
        text, error = self._tool("update_config", changes={"admin": doc["data"]["admin"]}, revision="stale")
        self.assertTrue(error)
        self.assertIn("изменился", text)
        self.assertIn("token", self._tool("config_schema", section="admin")[0]["properties"])

    def test_call_api_stays_in_api(self):
        self.assertTrue(self._tool("call_api", method="GET", path="/etc/passwd")[1])
        status, error = self._tool("call_api", method="GET", path="/api/status")
        self.assertFalse(error)
        self.assertTrue(status["running"])


LX_VERSION = """sing-box version 1.14.3-lx.21

Environment: go1.25.3 linux/amd64
Tags: with_gvisor,with_quic,with_utls,with_xhttp,with_awg
Revision: 0123abcd
CGO: disabled
"""


class CoreAndDocsTest(unittest.TestCase):
    def setUp(self):
        mcp._core_info.cache_clear()
        self.addCleanup(mcp._core_info.cache_clear)
        self.binary = os.path.join(temp_dir(), "sing-box")
        open(self.binary, "w").close()

    def _core(self, stdout):
        done = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with mock.patch.object(mcp.shutil, "which", return_value=self.binary), \
                mock.patch.object(mcp.subprocess, "run", return_value=done):
            return mcp.core_info(), mcp._instructions()

    def test_lx_core_links_its_docs(self):
        info, text = self._core(LX_VERSION)
        self.assertEqual(info["version"], "1.14.3-lx.21")
        self.assertIn("with_xhttp", info["tags"])
        self.assertIn("next to sing-box-lx 1.14.3-lx.21", text)
        self.assertIn("Leadaxe/sing-box-lx/blob/v1.14.3-lx.21/docs-lx/lx-config.md", text)
        self.assertIn("docs/CONFIG_ASSEMBLY.md", text)

    def test_official_core_and_missing_core(self):
        _, text = self._core("sing-box version 1.14.3\n")
        self.assertIn("SagerNet/sing-box/blob/v1.14.3/docs/configuration", text)
        with mock.patch.object(mcp.shutil, "which", return_value=None):
            self.assertEqual(mcp.core_info(), {})
            self.assertIn("https://sing-box.sagernet.org/configuration/", mcp._instructions())
        mcp._core_info.cache_clear()
        with mock.patch.object(mcp.shutil, "which", return_value=self.binary), \
                mock.patch.object(mcp.subprocess, "run", side_effect=subprocess.TimeoutExpired("sing-box", 10)):
            self.assertEqual(mcp.core_info(), {})
        self.assertEqual(self._core(LX_VERSION)[0]["version"], "1.14.3-lx.21")   # сбой не закэширован

    def test_docs_list_and_sections(self):
        listing = mcp.docs(None)["documents"]
        paths = {d["path"] for d in listing}
        self.assertIn("docs/CONFIG_ASSEMBLY.md", paths)
        self.assertFalse(any(p.endswith(".ru.md") for p in paths))
        merging = mcp.docs(None, "docs/CONFIG_ASSEMBLY.md", "merging")
        self.assertTrue(merging.startswith("## Merging"))
        self.assertNotIn("## Preset library", merging)
        with self.assertRaises(mcp.ToolError):
            mcp.docs(None, "../LICENSE")
        with self.assertRaises(mcp.ToolError):
            mcp.docs(None, section="merging")
        with self.assertRaises(mcp.ToolError):
            mcp.docs(None, "docs/CONFIG_ASSEMBLY.md", "no such heading")


if __name__ == "__main__":
    unittest.main()
