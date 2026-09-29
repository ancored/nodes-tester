"""Offline subprocess and scheduler checks; no router/network access."""

import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from nodes_admin.orchestrator import BusyError, Orchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.data = self.root / "data"
        self.schedule = {
            "version": 1,
            "jobs": {
                "router": {"enabled": False, "times": ["03:00"]},
                "clients": {"enabled": False, "times": []},
            },
            "timeout": 2,
        }
        (self.config / "pipeline.json").write_text(json.dumps(self.schedule), encoding="utf-8")

    @staticmethod
    def wait_done(orch, run_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            row = orch.run(run_id)
            if row["status"] != "running":
                return row
            time.sleep(0.03)
        raise AssertionError("subprocess did not finish")

    def test_success_log_busy_and_apply_hooks(self):
        hooks = []
        orch = Orchestrator(self.config, self.data,
                            command=[sys.executable, "-c", "import time; print('ready'); time.sleep(.3)"],
                            on_apply_start=lambda: hooks.append("start"),
                            on_apply_end=lambda: hooks.append("end"))
        run_id = orch.start_run("router", False)
        with self.assertRaises(BusyError):
            orch.start_run("clients", False)
        row = self.wait_done(orch, run_id)
        self.assertEqual(row["status"], "ok")
        self.assertIn("ready", orch.log(run_id)["text"])
        self.assertEqual(hooks, ["start", "end"])
        dry = orch.start_run("router", True)
        self.assertEqual(self.wait_done(orch, dry)["status"], "ok")
        self.assertEqual(hooks, ["start", "end"])
        orch.stop()

    def test_exit_75_and_timeout(self):
        busy = Orchestrator(self.config, self.data,
                            command=[sys.executable, "-c", "import sys; sys.exit(75)"])
        self.assertEqual(self.wait_done(busy, busy.start_run("clients", False))["status"], "busy")
        busy.stop()
        self.schedule["timeout"] = 1
        (self.config / "pipeline.json").write_text(json.dumps(self.schedule), encoding="utf-8")
        slow = Orchestrator(self.config, self.data,
                            command=[sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertEqual(self.wait_done(slow, slow.start_run("clients", False))["status"], "timeout")
        slow.stop()

    def test_recover_dead_pid_and_schedule_once(self):
        fake_now = [time.time()]
        orch = Orchestrator(self.config, self.data,
                            command=[sys.executable, "-c", "print('scheduled')"],
                            clock=lambda: fake_now[0])
        with closing(sqlite3.connect(str(orch.db_path))) as db, db:
            db.execute("""INSERT INTO runs(id,mode,dry_run,trigger,started,status,pid,log_path)
                          VALUES ('orphan','router',0,'manual',?,'running',999999,?)""",
                       (fake_now[0], str(orch.logs_dir / "orphan.log")))
        orch.stop()
        orch = Orchestrator(self.config, self.data,
                            command=[sys.executable, "-c", "print('scheduled')"],
                            clock=lambda: fake_now[0])
        self.assertEqual(orch.run("orphan")["status"], "interrupted")
        next_minute = time.localtime(fake_now[0] + 120)
        slot = f"{next_minute.tm_hour:02d}:{next_minute.tm_min:02d}"
        self.schedule["jobs"]["clients"] = {"enabled": True, "times": [slot]}
        _, revision = orch.read_schedule()
        orch.write_schedule(self.schedule, revision)
        fake_now[0] += 180
        orch.tick()
        current = next(row for row in orch.runs() if row["trigger"] == "schedule")
        self.wait_done(orch, current["id"])
        orch.tick()
        self.assertEqual(len([row for row in orch.runs() if row["trigger"] == "schedule"]), 1)
        orch.stop()

    def test_recover_exit_file(self):
        orch = Orchestrator(self.config, self.data)
        with closing(sqlite3.connect(str(orch.db_path))) as db, db:
            for code, expected in ((0, "ok"), (75, "busy"), (1, "error")):
                run_id = f"orphan-{code}"
                log_path = orch.logs_dir / f"{run_id}.log"
                Path(str(log_path) + ".exit").write_text(str(code), encoding="ascii")
                db.execute("""INSERT INTO runs(id,mode,dry_run,trigger,started,status,pid,log_path)
                              VALUES (?, 'clients', 0, 'manual', ?, 'running', 999999, ?)""",
                           (run_id, time.time(), str(log_path)))
        orch.stop()
        recovered = Orchestrator(self.config, self.data)
        for code, expected in ((0, "ok"), (75, "busy"), (1, "error")):
            row = recovered.run(f"orphan-{code}")
            self.assertEqual((row["status"], row["exit_code"]), (expected, code))
        recovered.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups only")
    def test_timeout_allows_term_ignoring_process_to_finish(self):
        self.schedule["timeout"] = 1
        (self.config / "pipeline.json").write_text(json.dumps(self.schedule), encoding="utf-8")
        command = [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(2)"]
        orch = Orchestrator(self.config, self.data, command=command, kill_grace=4)
        row = self.wait_done(orch, orch.start_run("clients", False))
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(row["exit_code"], 0)
        orch.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups only")
    def test_timeout_waits_for_descendant_before_ending_apply(self):
        self.schedule["timeout"] = 1
        (self.config / "pipeline.json").write_text(json.dumps(self.schedule), encoding="utf-8")
        flag = self.root / "finished"
        child = f"trap '' TERM; sleep 3; touch {shlex.quote(str(flag))}"
        command = ["sh", "-c", f"sh -c {shlex.quote(child)}; true"]
        hooks = []
        orch = Orchestrator(self.config, self.data, command=command, kill_grace=10,
                            on_apply_end=lambda: hooks.append(flag.exists()))
        row = self.wait_done(orch, orch.start_run("router", False))
        self.assertEqual(row["status"], "timeout")
        self.assertTrue(flag.exists())
        self.assertEqual(hooks, [True])
        orch.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups only")
    def test_timeout_kills_group_after_grace(self):
        self.schedule["timeout"] = 1
        (self.config / "pipeline.json").write_text(json.dumps(self.schedule), encoding="utf-8")
        flag = self.root / "finished"
        child = f"trap '' TERM; sleep 5; touch {shlex.quote(str(flag))}"
        command = ["sh", "-c", f"sh -c {shlex.quote(child)}; true"]
        orch = Orchestrator(self.config, self.data, command=command, kill_grace=1)
        row = self.wait_done(orch, orch.start_run("router", False))
        self.assertEqual(row["status"], "timeout")
        self.assertFalse(flag.exists())
        orch.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups only")
    def test_normal_exit_waits_for_descendant(self):
        flag = self.root / "finished"
        child = f"sleep 2; touch {shlex.quote(str(flag))}"
        command = ["sh", "-c", f"sh -c {shlex.quote(child)} &"]
        hooks = []
        orch = Orchestrator(self.config, self.data, command=command, kill_grace=5,
                            on_apply_end=lambda: hooks.append(flag.exists()))
        row = self.wait_done(orch, orch.start_run("router", False))
        self.assertEqual(row["status"], "ok")
        self.assertEqual(hooks, [True])
        orch.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups only")
    def test_recover_waits_for_orphaned_group(self):
        initial = Orchestrator(self.config, self.data)
        initial.stop()
        flag = self.root / "finished"
        child = f"sleep 2; touch {shlex.quote(str(flag))}"
        process = subprocess.Popen(["sh", "-c", f"sh -c {shlex.quote(child)} &"],
                                   start_new_session=True)
        self.assertEqual(process.wait(timeout=2), 0)
        log_path = initial.logs_dir / "orphan.log"
        Path(str(log_path) + ".exit").write_text("0", encoding="ascii")
        with closing(sqlite3.connect(str(initial.db_path))) as db, db:
            db.execute("""INSERT INTO runs(id,mode,dry_run,trigger,started,status,pid,log_path)
                          VALUES ('orphan','router',0,'manual',?,'running',?,?)""",
                       (time.time(), process.pid, str(log_path)))
        hooks = []
        recovered = Orchestrator(self.config, self.data,
                                 on_apply_start=lambda: hooks.append("start"),
                                 on_apply_end=lambda: hooks.append(flag.exists()))
        self.assertEqual(recovered.run("orphan")["status"], "running")
        row = self.wait_done(recovered, "orphan")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(hooks, ["start", True])
        recovered.stop()


if __name__ == "__main__":
    unittest.main()
