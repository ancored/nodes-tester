"""Базовый класс теста и реестр тестов."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import requests

# Заголовки обычного браузера для проверок доступности сервисов (gemini, openai,
# anthropic): запрос выглядит как от пользователя.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class TestContext:
    """Общие данные, доступные тесту во время прогона."""
    session: requests.Session          # сессия через SOCKS5 (текущая нода)
    node: str                          # тег тестируемой ноды
    default_timeout: float             # run.request_timeout
    regions: frozenset = frozenset()   # регионы ноды по измеренной стране — для for_region


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

    def option(self, ctx: TestContext, key: str, default=None):
        """Параметр теста: для ноды региона из for_region — оттуда, иначе общий."""
        for region, over in (self.options.get("for_region") or {}).items():
            if region in ctx.regions and key in over:
                return over[key]
        return self.options.get(key, default)

    def timeout(self, ctx: TestContext) -> float:
        return float(self.option(ctx, "timeout") or ctx.default_timeout)

    def url_for(self, ctx: TestContext) -> str:
        return self.option(ctx, "url") or self.default_url

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
