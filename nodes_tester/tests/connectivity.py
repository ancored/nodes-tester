"""Проверка выхода в интернет через ноду + определение IP/страны.

В конфиге выбирается СЕРВИС (не url): url и парсер ответа захардкожены per-service,
ответ нормализуется в единый набор метрик (exit_ip, country, опц. colo/asn/as_org).
Новый сервис = добавить пару в SERVICES + парсер.
"""

from __future__ import annotations

import json

import requests

from .base import BaseTest, TestContext, TestResult, register


def _parse_cloudflare(text: str) -> dict:
    """cloudflare.com/cdn-cgi/trace — строки key=value."""
    kv: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()
    out: dict = {}
    if kv.get("ip"):
        out["exit_ip"] = kv["ip"]
    if kv.get("loc"):
        out["country"] = kv["loc"].upper()
    if kv.get("colo"):
        out["colo"] = kv["colo"]
    return out


def _parse_curlmyip(text: str) -> dict:
    """curlmyip.ru/geo — JSON (GeoLite2 City/ASN)."""
    d = json.loads(text)
    out: dict = {}
    if d.get("ip"):
        out["exit_ip"] = d["ip"]
    cc = (d.get("country") or {}).get("iso_code")
    if cc:
        out["country"] = cc.upper()
    if d.get("autonomous_system_number"):
        out["asn"] = d["autonomous_system_number"]
    if d.get("autonomous_system_organization"):
        out["as_org"] = d["autonomous_system_organization"]
    return out


# Реестр сервисов: имя -> (url, парсер ответа в нормализованные метрики).
SERVICES: dict[str, tuple] = {
    "cloudflare": ("https://cloudflare.com/cdn-cgi/trace", _parse_cloudflare),
    "curlmyip":   ("https://curlmyip.ru/geo",              _parse_curlmyip),
}

DEFAULT_SERVICE = "cloudflare"


@register
class ConnectivityTest(BaseTest):
    name = "connectivity"

    def _service(self, ctx: TestContext) -> str:
        by_region = self.options.get("service_by_region") or {}
        return by_region.get(ctx.region) or self.options.get("service") or DEFAULT_SERVICE

    def run(self, ctx: TestContext) -> TestResult:
        name = self._service(ctx)
        entry = SERVICES.get(name)
        if entry is None:
            return TestResult(self.name, ok=False,
                              error=f"неизвестный сервис connectivity: '{name}'", url=name)
        url, parser = entry
        try:
            resp = ctx.session.get(url, timeout=self.timeout(ctx))
            resp.raise_for_status()
        except requests.RequestException as exc:
            return TestResult(self.name, ok=False, error=str(exc), url=url)

        metrics: dict = {"status": resp.status_code, "service": name}
        try:
            metrics.update(parser(resp.text))
        except (ValueError, KeyError, TypeError):
            pass  # битый формат ответа → OK без гео (тест про доступность)
        return TestResult(self.name, ok=True, metrics=metrics, url=url)
