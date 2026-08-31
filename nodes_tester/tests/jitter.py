"""Несколько замеров латентности подряд: avg/min/max, jitter, packet loss."""

from __future__ import annotations

import statistics
import time

import requests

from .base import BaseTest, TestContext, TestResult, register
from .latency import measure_ttfb

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=0"


@register
class JitterTest(BaseTest):
    name = "jitter"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        samples = max(1, int(self.options.get("samples", 5)))      # >0 (валидация)
        interval = max(0.0, float(self.options.get("interval", 0.2)))
        timeout = self.timeout(ctx)

        values: list[float] = []
        failures = 0
        for i in range(samples):
            try:
                values.append(measure_ttfb(ctx.session, url, timeout))
            except requests.RequestException:
                failures += 1
            if interval and i < samples - 1:
                time.sleep(interval)

        loss_pct = round(100.0 * failures / samples, 1) if samples else 100.0
        metrics: dict = {
            "samples": samples,
            "success": len(values),
            "loss_pct": loss_pct,
        }
        if values:
            metrics["avg_ms"] = round(statistics.fmean(values), 1)
            metrics["min_ms"] = round(min(values), 1)
            metrics["max_ms"] = round(max(values), 1)
            metrics["jitter_ms"] = (
                round(statistics.stdev(values), 1) if len(values) > 1 else 0.0
            )

        # Тест провален только если ни один замер не прошёл.
        ok = len(values) > 0
        error = None if ok else "все замеры латентности провалились"
        return TestResult(self.name, ok=ok, metrics=metrics, error=error, url=url)
