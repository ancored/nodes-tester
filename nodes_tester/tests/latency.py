"""Замер латентности (TTFB — time to first byte)."""

from __future__ import annotations

import time

import requests

from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=0"


def measure_ttfb(session: requests.Session, url: str, timeout: float) -> float:
    """Вернуть TTFB в миллисекундах. Бросает requests.RequestException."""
    start = time.monotonic()
    resp = session.get(url, timeout=timeout, stream=True)
    try:
        # Первый чтимый байт тела (после заголовков) == TTFB.
        next(resp.iter_content(chunk_size=1), b"")
        elapsed = time.monotonic() - start
        resp.raise_for_status()
    finally:
        resp.close()
    return elapsed * 1000.0


@register
class LatencyTest(BaseTest):
    name = "latency"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        try:
            ttfb = measure_ttfb(ctx.session, url, self.timeout(ctx))
        except requests.RequestException as exc:
            return TestResult(self.name, ok=False, error=str(exc), url=url)
        return TestResult(
            self.name, ok=True, metrics={"ttfb_ms": round(ttfb, 1)}, url=url
        )
