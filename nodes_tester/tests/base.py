"""Базовый класс теста и реестр тестов."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests


@dataclass
class TestContext:
    """Общие данные, доступные тесту во время прогона."""
    session: requests.Session          # сессия через SOCKS5 (текущая нода)
    node: str                          # тег тестируемой ноды
    default_timeout: float             # run.request_timeout
    region: str = ""                   # коарс-регион (eu/us/ru/other) — для выбора url


@dataclass
class TestResult:
    name: str
    ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    url: Optional[str] = None          # url, по которому реально шёл тест

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        if self.url is not None:
            out["url"] = self.url
        out.update(self.metrics)
        if self.error:
            out["error"] = self.error
        return out


class BaseTest:
    """Наследники объявляют name/default_url и реализуют run()."""
    name: str = "base"
    default_url: str = ""

    def __init__(self, options: dict[str, Any]):
        self.options = options or {}
        try:
            self.every = max(1, int(self.options.get("every", 1) or 1))
        except (TypeError, ValueError):
            self.every = 1

    def due(self, pass_no: int) -> bool:
        """Запускать ли тест на этом прогоне (для тяжёлых тестов раз в N)."""
        return self.every <= 1 or ((pass_no - 1) % self.every) == 0

    def timeout(self, ctx: TestContext) -> float:
        return float(self.options.get("timeout", ctx.default_timeout))

    def url_for(self, ctx: TestContext) -> str:
        """URL теста с учётом переопределения по региону.

        Приоритет: url_by_region[регион] -> url -> default_url класса.
        """
        by_region = self.options.get("url_by_region") or {}
        return by_region.get(ctx.region) or self.options.get("url") or self.default_url

    def run(self, ctx: TestContext) -> TestResult:  # pragma: no cover
        raise NotImplementedError


# --- Реестр ---------------------------------------------------------------

_REGISTRY: dict[str, type[BaseTest]] = {}


def register(cls: type[BaseTest]) -> type[BaseTest]:
    _REGISTRY[cls.name] = cls
    return cls


def get_test_class(name: str) -> Optional[type[BaseTest]]:
    return _REGISTRY.get(name)


def known_tests() -> list[str]:
    return sorted(_REGISTRY)
