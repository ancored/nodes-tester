"""Кольцевой буфер логов процесса + tee-поток для перехвата print (админка /api/logs).

Процесс тестера пишет через print в sys.stdout/sys.stderr; для живого лога в
веб-админке мы подменяем оба потока на TeeStream, который дублирует строки в
LogRing (и продолжает писать в реальный терминал). LogRing потокобезопасен:
пишут основной поток прогона, фоновые потоки (monitor/traffic) и чтение идёт из
потока HTTP-сервера админки.
"""

from __future__ import annotations


class LogRing:
    """Потокобезопасный кольцевой буфер строк с монотонным порядковым номером seq.

    seq растёт на каждую СТРОКУ (не на запись), поэтому клиент админки может
    запрашивать только новые строки через `since(seq)` (long-poll без SSE).
    """

    def __init__(self, maxlen: int = 2000):
        self._entries: list[dict] = []
        self._lock = __import__("threading").Lock()
        self._maxlen = maxlen
        self._seq = 0
        self._buf = ""

    def write(self, s: str) -> None:
        """Накопление строк: print шлёт текст и перевод отдельными write()."""
        if not s:
            return
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._append(line)
            # Очень длинная строка без перевода — не даём буферу распухнуть.
            if len(self._buf) > 8192:
                self._append(self._buf)
                self._buf = ""

    def _append(self, line: str) -> None:
        if not line:
            return
        self._seq += 1
        self._entries.append({"seq": self._seq, "ts": __import__("time").time(),
                              "line": line})
        if len(self._entries) > self._maxlen:
            self._entries = self._entries[-self._maxlen:]

    def flush(self) -> None:
        pass  # tee-интерфейс (file-like): явно буферизовать нечего

    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._entries if e["seq"] > seq]


class TeeStream:
    """Прокси-поток: пишет и в реальный stdout/stderr, и в LogRing.

    Незнакомые атрибуты (encoding, fileno, isatty, reconfigure, closed, …)
    делегируются реальному потоку через __getattr__, поэтому подмена прозрачна
    для print/сторонних библиотек.
    """

    def __init__(self, ring: LogRing, real):
        self._ring = ring
        self._real = real

    def write(self, s):
        try:
            self._real.write(s)
        finally:
            self._ring.write(s)

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)
