"""Тип выходного IP ноды: провайдер (ASN), дата-центр, мобильная сеть, известный прокси.

Выходной IP даёт тест connectivity (exit_ip). После прогона новые или устаревшие IP
проверяются одним пакетным запросом к ip-api.com (бесплатный тариф: только HTTP, до 100
IP в запросе, 15 запросов в минуту) — через SOCKS тестера на здоровой ноде (DNS-фильтры
роутера часто блокируют ip-api.com), при неудаче напрямую. Результат кэшируется в таблице
ip_info на `ttl_days`.

Тип для показа:
  mobile      — мобильная сеть;
  hosting     — дата-центр/хостинг (часть сервисов режет такие IP);
  residential — ни то, ни другое (домашний/провайдерский);
proxy=1 — IP известен как VPN/прокси/Tor.
"""

from __future__ import annotations

import requests

_FIELDS = "status,message,query,countryCode,isp,org,as,asname,mobile,proxy,hosting"
_BATCH = 100


def kind(info: dict) -> str:
    if info.get("mobile"):
        return "mobile"
    if info.get("hosting"):
        return "hosting"
    return "residential"


def _asn(value: str) -> "int | None":
    head = (value or "").split(" ", 1)[0]
    if head.upper().startswith("AS") and head[2:].isdigit():
        return int(head[2:])
    return None


def lookup(ips, url: str, timeout: float = 10.0, session=None) -> dict:
    """{ip: {asn, as_name, isp, org, country, mobile, proxy, hosting}} для распознанных IP.
    Сетевые сбои не бросаются наружу: вернётся то, что успели получить."""
    out: dict = {}
    ips = [ip for ip in dict.fromkeys(ips) if ip]
    http = session or requests
    for start in range(0, len(ips), _BATCH):
        chunk = ips[start:start + _BATCH]
        try:
            resp = http.post(url, params={"fields": _FIELDS}, json=chunk, timeout=timeout)
            resp.raise_for_status()
            rows = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"  [ipinfo] не получено ({type(exc).__name__}): {exc}")
            break
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("status") != "success" or not row.get("query"):
                continue
            out[row["query"]] = {
                "asn": _asn(row.get("as", "")),
                "as_name": row.get("asname") or "",
                "isp": row.get("isp") or "",
                "org": row.get("org") or "",
                "country": (row.get("countryCode") or "").upper(),
                "mobile": 1 if row.get("mobile") else 0,
                "proxy": 1 if row.get("proxy") else 0,
                "hosting": 1 if row.get("hosting") else 0,
            }
    return out
