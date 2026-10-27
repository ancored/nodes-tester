"""Причина провала проверки ноды одним кодом.

Тестер видит только то, что вернул SOCKS sing-box: обрыв или таймаут без подробностей.
Настоящая причина — в логе sing-box, в строке ошибки соединения через тестовый селектор
("dial tcp …: i/o timeout", "lookup …", "x509: …"). CoreErrors берёт такие строки по времени
теста, тестер сохраняет лучшую рядом с результатом (`core_error`), а cause() сводит её и
ошибку клиента к коду из CAUSES. Админка переводит код в слова.
"""

from __future__ import annotations

import re
import time

from .monitor import parse_error

# Порядок — от самой точной причины к общей: из строк лога за время теста берётся первая
# по этому списку.
CAUSES = ("dns", "tls", "tcp", "transport", "handshake", "reset", "freeze", "slow",
          "service", "target", "timeout", "other")

_TCP = re.compile(r"dial (?:tcp|udp) \S+: (?:i/o timeout|connect: )")
_READ_TIMEOUT = re.compile(r"read tcp \S+: i/o timeout")
_HTTP_STATUS = re.compile(r"\b([45]\d\d) (?:Client|Server) Error|^HTTP ([45]\d\d)")
_AI_TESTS = {"gemini", "openai", "anthropic"}
_READ_LIMIT = 2_000_000


def core_cause(error: str) -> "str | None":
    """Код причины по строке ошибки sing-box или None — строка ничего не говорит о ноде
    (соединение отменил сам клиент)."""
    if not error or "canceled" in error:
        return None
    if error.startswith("lookup "):
        return "dns"
    if "x509:" in error or "reality verification" in error or "tls: " in error:
        return "tls"
    if _TCP.match(error):
        return "tcp"
    if "unexpected HTTP response status" in error:
        return "transport"
    if "context deadline exceeded" in error:
        return "handshake"
    if _READ_TIMEOUT.match(error):
        return "freeze"
    if "EOF" in error or "reset by peer" in error or "transport closed" in error:
        return "reset"
    return "other"


def cause(test: str, metrics: dict, error: str) -> "str | None":
    """Код причины провала или None — у проверки нет «причины» (белые списки: нода просто
    не в списке). Сначала то, что видно при живом туннеле (отказ сервиса, низкая скорость,
    ошибка цели), потом ошибка sing-box, потом — по ошибке клиента."""
    if test == "whitelist":
        return None
    error = error or ""
    if test in _AI_TESTS and ("недоступен в регионе" in error or "видит страну" in error
                              or error.startswith("HTTP 4")):
        return "service"
    if "ниже порога" in error:
        return "slow"
    status = _HTTP_STATUS.search(error)
    if status and test not in _AI_TESTS:
        return "target"
    core = core_cause(metrics.get("core_error") or "")
    if core:
        return core
    if "IncompleteRead" in error or "неполная закачка" in error:
        return "freeze"
    if "WRONG_VERSION_NUMBER" in error or "CERTIFICATE_VERIFY_FAILED" in error:
        return "tls"
    if "EOF" in error or "reset by peer" in error or "закрыл соединение" in error:
        return "reset"
    if "timed out" in error:
        return "timeout"
    return "other"


class CoreErrors:
    """Ошибки соединений через тестовый селектор из лога sing-box за время теста.
    Только чтение: обрезает лог монитор (_LogTail), здесь файл стал короче — читаем с начала."""

    def __init__(self, path: str, selector: str):
        self.path = path
        self.selector = selector

    def mark(self) -> tuple:
        """Отметка перед тестом: (позиция в файле, время)."""
        try:
            with open(self.path, "rb") as fh:
                return fh.seek(0, 2), time.time()
        except OSError:
            return None, time.time()

    def since(self, mark: tuple) -> str:
        """Самая точная ошибка соединения, начатого после отметки; нет — пустая строка."""
        pos, started = mark
        if pos is None:
            return ""
        try:
            with open(self.path, "rb") as fh:
                end = fh.seek(0, 2)
                fh.seek(pos if end >= pos else 0)
                text = fh.read(_READ_LIMIT).decode("utf-8", "replace")
        except OSError:
            return ""
        now, best = time.time(), None
        for line in text.splitlines():
            parsed = parse_error(line, now)
            # Метка времени в логе — до секунды: запас 2 с.
            if not parsed or parsed[0] != self.selector or parsed[3] < started - 2:
                continue
            code = core_cause(parsed[1])
            if code and (best is None or CAUSES.index(code) < CAUSES.index(best[0])):
                best = code, parsed[1]
        return best[1] if best else ""
