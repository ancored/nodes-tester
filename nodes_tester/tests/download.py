"""Замер скорости скачивания через ноду (Мбит/с)."""

from __future__ import annotations

import time

import requests

from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
CHUNK = 64 * 1024


@register
class DownloadTest(BaseTest):
    name = "download"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        timeout = self.timeout(ctx)

        downloaded = 0
        start = time.monotonic()
        try:
            with ctx.session.get(url, timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    downloaded += len(chunk)
        except requests.RequestException as exc:
            return TestResult(
                self.name, ok=False,
                metrics={"bytes": downloaded}, error=str(exc), url=url,
            )

        elapsed = time.monotonic() - start
        if elapsed <= 0 or downloaded == 0:
            return TestResult(
                self.name, ok=False,
                metrics={"bytes": downloaded},
                error="нулевой объём или время скачивания", url=url,
            )

        mbps = (downloaded * 8) / elapsed / 1_000_000  # Мбит/с
        return TestResult(
            self.name, ok=True,
            metrics={
                "bytes": downloaded,
                "seconds": round(elapsed, 2),
                "speed_mbps": round(mbps, 2),
            },
            url=url,
        )
