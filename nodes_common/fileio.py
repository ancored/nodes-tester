"""Атомарная запись и межпроцессная блокировка файлов-артефактов.

Артефакты (raw_nodes.json, nodes.json) пишут и демон-оркестратор, и ручной запуск CLI —
запись должна быть атомарной (читатель видит старый ИЛИ новый файл, не обрывок), а два
писателя одного файла не должны работать одновременно.
"""

import contextlib
import json
import os
import time
from datetime import datetime


def parse_iso(value):
    """ISO-8601 (в т.ч. с Z) → aware datetime; None/мусор → None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def dumps_json(obj):
    """Каноничная сериализация артефактов: отступ 2, UTF-8 как есть, перевод строки в конце."""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path, text):
    """temp в том же каталоге → flush+fsync → os.replace. При сбое старый файл цел."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class LockTimeout(RuntimeError):
    pass


@contextlib.contextmanager
def file_lock(path, timeout=60.0):
    """Эксклюзивная блокировка `<path>.lock` (fcntl на Linux, msvcrt на Windows).
    Ждёт до timeout секунд, затем LockTimeout. Блокировка снимается ОС при смерти процесса."""
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"файл занят другим процессом: {lock_path}")
                time.sleep(0.2)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


if os.name == "nt":
    import msvcrt

    def _lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
