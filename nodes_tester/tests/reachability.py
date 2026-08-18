"""Multi-endpoint reachability: проверяем набор целей (нейтральные connectivity-
проверки + типично-блокируемые в РФ). Ловит селективную блокировку: нода жива,
но что-то важное недоступно.

Цели проверяются ПАРАЛЛЕЛЬНО (иначе 15 целей × таймаут затянули бы прогон,
особенно на мёртвой ноде).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URLS = [
    "https://www.google.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://www.youtube.com/generate_204",
]


@register
class ReachabilityTest(BaseTest):
    name = "reachability"

    def _urls(self, ctx: TestContext) -> list[str]:
        by_region = self.options.get("urls_by_region") or {}
        return by_region.get(ctx.region) or self.options.get("urls") or DEFAULT_URLS

    def run(self, ctx: TestContext) -> TestResult:
        urls = self._urls(ctx)
        timeout = self.timeout(ctx)
        workers = int(self.options.get("workers", 8))

        def probe(url: str):
            try:
                resp = ctx.session.get(url, timeout=timeout, stream=True)
                resp.close()
                return url, resp.status_code, resp.status_code < 500
            except requests.RequestException:
                return url, "err", False

        endpoints: dict[str, object] = {}
        reached = 0
        if urls:
            with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
                for url, status, ok in pool.map(probe, urls):
                    endpoints[url] = status
                    if ok:
                        reached += 1

        total = len(urls)
        loss = round(100.0 * (total - reached) / total, 1) if total else 100.0
        return TestResult(
            self.name, ok=(reached > 0),
            metrics={
                "total": total,
                "reached": reached,
                "loss_pct": loss,
                "endpoints": endpoints,
            },
            url=f"{total} endpoints",
        )
