"""Disk-backed, allowlisted router/client pipeline launcher."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MODES = ("router", "clients")          # по расписанию
RUN_MODES = MODES + ("apply", "apply-clients")  # вручную, без загрузки подписок: база и пресеты
                                                # к текущему nodes.json / клиенты к whnodes.json
APPLY_MODES = ("router", "apply")      # меняют sing-box роутера: тестер на паузе
STATUSES = ("running", "ok", "error", "busy", "timeout", "interrupted")


class BusyError(Exception):
    pass


def validate_schedule(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {"version", "jobs", "timeout"} or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("pipeline.json: ожидаются version=1, jobs и timeout")
    timeout = data["timeout"]
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("pipeline.json: timeout должен быть 1..86400 с")
    jobs = data["jobs"]
    if not isinstance(jobs, dict) or set(jobs) != set(MODES):
        raise ValueError("pipeline.json: нужны задания router и clients")
    for mode in MODES:
        job = jobs[mode]
        if not isinstance(job, dict) or set(job) != {"enabled", "times"}:
            raise ValueError(f"pipeline.json: {mode} требует enabled и times")
        times = job["times"]
        if type(job["enabled"]) is not bool or not isinstance(times, list):
            raise ValueError(f"pipeline.json: неверные enabled/times для {mode}")
        for value in times:
            if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                raise ValueError(f"pipeline.json: время {value!r} не HH:MM")
            hour, minute = value[:2], value[3:]
            if not (hour.isascii() and minute.isascii() and hour.isdigit() and minute.isdigit()
                    and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError(f"pipeline.json: время {value!r} не HH:MM")
        if len(times) != len(set(times)):
            raise ValueError(f"pipeline.json: повтор времени для {mode}")
    return data


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exit_status(code: int) -> str:
    """Код выхода конвейера → статус прогона (75 — конвейер уже занят)."""
    if code == 75:
        return "busy"
    if code == 0:
        return "ok"
    return "error"


def _slot_stamps(times, days) -> list[float]:
    """Метки времени слотов расписания HH:MM в указанные дни (локальное время)."""
    stamps = []
    for day in days:
        for value in times:
            hour, minute = map(int, value.split(":"))
            stamps.append(dt.datetime.combine(day, dt.time(hour, minute)).timestamp())
    return stamps


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates a process on Windows. Query the handle.
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _process_start(pid: int):
    """Linux start ticks distinguish an orphan from a reused PID."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return raw.rsplit(") ", 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def _same_process(pid: int, start: str) -> bool:
    return bool(start and _pid_alive(pid) and _process_start(pid) == start)


def _group_alive(pgid: int) -> bool:
    """Whether a POSIX process group still has work to do."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if os.path.isdir("/proc"):
        # A reparented zombie may keep killpg(0) successful after all work ended.
        for stat in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = stat.read_text(encoding="ascii").rsplit(") ", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] != "Z":
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    return True


def _run_alive(pid: int, start: str) -> bool:
    return _group_alive(pid) if os.name == "posix" else _same_process(pid, start)


class Orchestrator:
    def __init__(self, config_dir: Path, data_dir: Path, *, command=None,
                 on_apply_start=None, on_apply_end=None, on_finish=None, clock=time.time,
                 kill_grace=300):
        self.config_dir = Path(config_dir)
        self.data_dir = Path(data_dir)
        self.schedule_path = self.config_dir / "pipeline.json"
        self.db_path = self.data_dir / "pipeline.db"
        self.logs_dir = self.data_dir / "pipeline/runs"
        self.command = command or ["nodes-tester", "pipeline"]
        self.on_apply_start = on_apply_start or (lambda: None)
        self.on_apply_end = on_apply_end or (lambda: None)
        # Прогон завершён: on_finish(row) — строка runs (id, mode, dry_run, status, log_path…).
        self.on_finish = on_finish or (lambda row: None)
        self.clock = clock
        self.kill_grace = kill_grace
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._workers = {}
        self.read_schedule()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._recover()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self):
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, mode TEXT NOT NULL, dry_run INTEGER NOT NULL,
                trigger TEXT NOT NULL, started REAL NOT NULL, finished REAL,
                status TEXT NOT NULL, exit_code INTEGER, pid INTEGER, pid_start TEXT,
                log_path TEXT NOT NULL,
                scheduled_for REAL)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "pid_start" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN pid_start TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS schedule_state (
                mode TEXT PRIMARY KEY, last_slot REAL NOT NULL)""")
            now = self.clock()
            for mode in MODES:
                db.execute("INSERT OR IGNORE INTO schedule_state(mode,last_slot) VALUES (?,?)", (mode, now))

    def _recover(self):
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT id,mode,dry_run,pid,pid_start,log_path FROM runs WHERE status='running'").fetchall()
            for row in rows:
                if row["pid"] and _run_alive(row["pid"], row["pid_start"]):
                    if row["mode"] in APPLY_MODES and not row["dry_run"]:
                        self.on_apply_start()
                    worker = threading.Thread(target=self._watch_orphan,
                                              args=(row["id"], row["pid"], row["pid_start"], row["mode"], bool(row["dry_run"])),
                                              daemon=True)
                    self._workers[row["id"]] = worker
                    worker.start()
                else:
                    status, code = self._exit_result(row["log_path"])
                    db.execute("UPDATE runs SET status=?,exit_code=?,finished=? WHERE id=?",
                               (status, code, self.clock(), row["id"]))
            self._prune(db)

    def _watch_orphan(self, run_id, pid, pid_start, mode, dry_run):
        completed = False
        try:
            while _run_alive(pid, pid_start) and not self._stop.wait(2):
                pass
            if not _run_alive(pid, pid_start):
                row = self.run(run_id)
                status, code = self._exit_result(row["log_path"])
                self._finish(run_id, status, code)
                completed = True
        finally:
            if completed and mode in APPLY_MODES and not dry_run:
                self.on_apply_end()
            self._workers.pop(run_id, None)

    def _active(self, db):
        return db.execute("SELECT id FROM runs WHERE status='running' LIMIT 1").fetchone()

    @staticmethod
    def _exit_result(log_path):
        try:
            code = int(Path(str(log_path) + ".exit").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return "interrupted", None
        return _exit_status(code), code

    def start_run(self, mode: str, dry_run: bool, *, trigger="manual", scheduled_for=None) -> str:
        if (mode not in RUN_MODES or type(dry_run) is not bool
                or trigger not in ("manual", "schedule") or (trigger == "schedule" and mode not in MODES)):
            raise ValueError("разрешены router|clients|apply и dry_run: bool")
        with self._lock, self._connect() as db:
            if self._active(db):
                raise BusyError("конвейер уже выполняется")
            run_id = uuid.uuid4().hex
            log_path = self.logs_dir / (run_id + ".log")
            db.execute("""INSERT INTO runs(id,mode,dry_run,trigger,started,status,log_path,scheduled_for)
                          VALUES (?,?,?,?,?,'running',?,?)""",
                       (run_id, mode, int(dry_run), trigger, self.clock(), str(log_path), scheduled_for))
            worker = threading.Thread(target=self._execute,
                                      args=(run_id, mode, dry_run, log_path), daemon=True)
            self._workers[run_id] = worker
            worker.start()
            return run_id

    def _execute(self, run_id, mode, dry_run, log_path):
        applying = mode in APPLY_MODES and not dry_run
        try:
            if applying:
                self.on_apply_start()
            args = [*self.command, mode] + (["--dry-run"] if dry_run else [])
            timeout = self.read_schedule()[0]["timeout"]
            with log_path.open("wb") as log:
                env = os.environ.copy()
                env["PIPELINE_EXIT_FILE"] = str(log_path) + ".exit"
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=(os.name == "posix"), env=env)
                with self._lock, self._connect() as db:
                    db.execute("UPDATE runs SET pid=?,pid_start=? WHERE id=?",
                               (process.pid, _process_start(process.pid), run_id))
                try:
                    code = process.wait(timeout=timeout)
                    status = _exit_status(code)
                    if os.name == "posix" and not self._wait_group(process, self.kill_grace):
                        log.write(b"\n[orchestrator] process group still active after parent exit; killing descendants\n")
                        log.flush()
                        self._kill_group(process)
                        status = "error"
                except subprocess.TimeoutExpired:
                    self._terminate_group(process, self.kill_grace)
                    code, status = process.returncode, "timeout"
            self._finish(run_id, status, code)
        except Exception as exc:
            with log_path.open("ab") as log:
                log.write((f"\n[orchestrator] {type(exc).__name__}: {exc}\n").encode("utf-8"))
            self._finish(run_id, "error", None)
        finally:
            if applying:
                self.on_apply_end()
            self._workers.pop(run_id, None)

    @staticmethod
    def _wait_group(process, grace):
        deadline = time.monotonic() + grace
        while True:
            process.poll()  # Reap the direct child even if its descendants remain.
            if not _group_alive(process.pid):
                process.wait()
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(1, remaining))

    @classmethod
    def _kill_group(cls, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        cls._wait_group(process, float("inf"))

    @classmethod
    def _terminate_group(cls, process, kill_grace=300):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not cls._wait_group(process, kill_grace):
                cls._kill_group(process)
            return
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            if process.poll() is not None:
                return
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()

    def _finish(self, run_id, status, code):
        with self._lock, self._connect() as db:
            done = db.execute("UPDATE runs SET finished=?,status=?,exit_code=? WHERE id=? AND status='running'",
                              (self.clock(), status, code, run_id)).rowcount
            self._prune(db)
        if done:
            try:
                self.on_finish(self.run(run_id))
            except Exception as exc:  # noqa: BLE001 — наблюдатель не должен ронять оркестратор
                print(f"[pipeline] обработчик завершения прогона: {type(exc).__name__}: {exc}")

    def _prune(self, db):
        old = db.execute("SELECT id,log_path FROM runs WHERE status!='running' ORDER BY started DESC LIMIT -1 OFFSET 100").fetchall()
        for row in old:
            db.execute("DELETE FROM runs WHERE id=?", (row["id"],))
            Path(row["log_path"]).unlink(missing_ok=True)
            Path(row["log_path"] + ".exit").unlink(missing_ok=True)

    def read_schedule(self):
        raw = self.schedule_path.read_bytes()
        return validate_schedule(json.loads(raw.decode("utf-8"))), _revision(raw)

    def write_schedule(self, data, revision):
        validate_schedule(data)
        with self._lock:
            _old, current = self.read_schedule()
            if revision != current:
                raise ValueError("revision_conflict")
            text = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            temp = self.schedule_path.with_name("." + self.schedule_path.name + ".tmp")
            temp.write_bytes(text)
            os.replace(temp, self.schedule_path)
            # A new or edited schedule starts with future slots; restart catch-up
            # applies only to slots missed after this point.
            with self._connect() as db:
                for mode in MODES:
                    db.execute("UPDATE schedule_state SET last_slot=? WHERE mode=?", (self.clock(), mode))
            return _revision(text)

    def _latest_due(self, times, now):
        today = dt.datetime.fromtimestamp(now).date()
        due = [s for s in _slot_stamps(times, (today - dt.timedelta(days=1), today)) if s <= now]
        return max(due) if due else None

    def next_run(self, mode, now=None):
        now = self.clock() if now is None else now
        data, _ = self.read_schedule()
        job = data["jobs"][mode]
        if not job["enabled"] or not job["times"]:
            return None
        today = dt.datetime.fromtimestamp(now).date()
        future = [s for s in _slot_stamps(job["times"], (today, today + dt.timedelta(days=1))) if s > now]
        return min(future) if future else None

    def tick(self):
        now = self.clock()
        data, _ = self.read_schedule()
        for mode in MODES:
            job = data["jobs"][mode]
            if not job["enabled"] or not job["times"]:
                continue
            slot = self._latest_due(job["times"], now)
            with self._lock, self._connect() as db:
                last = db.execute("SELECT last_slot FROM schedule_state WHERE mode=?", (mode,)).fetchone()[0]
                if slot is None or slot <= last:
                    continue
                db.execute("UPDATE schedule_state SET last_slot=? WHERE mode=?", (slot, mode))
            try:
                self.start_run(mode, False, trigger="schedule", scheduled_for=slot)
            except BusyError:
                run_id = uuid.uuid4().hex
                with self._lock, self._connect() as db:
                    db.execute("""INSERT INTO runs(id,mode,dry_run,trigger,started,finished,status,log_path,scheduled_for)
                                  VALUES (?,?,0,'schedule',?,?,'busy',?,?)""",
                               (run_id, mode, now, now, str(self.logs_dir / (run_id + ".log")), slot))
                    self._prune(db)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="pipeline-scheduler", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(5):
            try:
                self.tick()
            except (OSError, ValueError) as exc:
                print(f"[pipeline] ошибка расписания: {exc}")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def runs(self, limit=20):
        limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def run(self, run_id):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def log(self, run_id, offset=0):
        row = self.run(run_id)
        if row is None:
            raise KeyError(run_id)
        path = Path(row["log_path"])
        if not path.is_file():
            return {"text": "", "offset": 0}
        with path.open("rb") as stream:
            stream.seek(min(max(0, int(offset)), path.stat().st_size))
            payload = stream.read(64 * 1024)
            return {"text": payload.decode("utf-8", errors="replace"), "offset": stream.tell()}

    def status(self):
        data, revision = self.read_schedule()
        with self._lock, self._connect() as db:
            current = db.execute("SELECT * FROM runs WHERE status='running' ORDER BY started DESC LIMIT 1").fetchone()
            last = {mode: db.execute("SELECT * FROM runs WHERE mode=? ORDER BY started DESC LIMIT 1",
                                     (mode,)).fetchone() for mode in RUN_MODES}
        return {
            "schedule": data,
            "revision": revision,
            "next_run": {mode: self.next_run(mode) for mode in MODES},
            "current": dict(current) if current else None,
            "last": {mode: dict(row) if row else None for mode, row in last.items()},
        }
