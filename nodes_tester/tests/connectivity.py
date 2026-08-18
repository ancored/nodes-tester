"""Проверка выхода в интернет через ноду + определение IP/страны."""

from __future__ import annotations

import requests

from .base import BaseTest, TestContext, TestResult, register

DEFAULT_URL = "https://cloudflare.com/cdn-cgi/trace"


def _parse_trace(text: str) -> dict[str, str]:
    """Разобрать ответ cloudflare trace (строки key=value)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@register
class ConnectivityTest(BaseTest):
    name = "connectivity"
    default_url = DEFAULT_URL

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        try:
            resp = ctx.session.get(url, timeout=self.timeout(ctx))
            resp.raise_for_status()
        except requests.RequestException as exc:
            return TestResult(self.name, ok=False, error=str(exc), url=url)

        metrics: dict = {"status": resp.status_code}
        trace = _parse_trace(resp.text)
        if trace:
            # cloudflare trace: ip=..., loc=<страна>, colo=<дата-центр>
            if "ip" in trace:
                metrics["exit_ip"] = trace["ip"]
            if "loc" in trace:
                metrics["country"] = trace["loc"]
            if "colo" in trace:
                metrics["colo"] = trace["colo"]
        return TestResult(self.name, ok=True, metrics=metrics, url=url)
