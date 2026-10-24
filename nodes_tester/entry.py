"""Вход ноды: IP сервера, к которому подключается sing-box, и цепочки «вход → выход».

Раз в 5 минут тестер резолвит адреса серверов нод без свежих сведений — через DoH-серверы
группы `bootstrap` конфига sing-box (ею sing-box резолвит домены нод; нет такой группы —
Cloudflare), только IPv4. Ответ кэшируется в таблице entry_dns на `checks.ip_type.ttl`;
страну и AS входных IP даёт тот же запрос к ip-api, что и для выходных (таблица ip_info).

Цепочка — выходной IP ноды не совпадает ни с одним IP входа: сервер подписки передаёт
трафик дальше. Выход через Cloudflare WARP (AS13335) помечается отдельно: страна такого
выхода зависит от точки присутствия Cloudflare.
"""

from __future__ import annotations

import ipaddress
import struct
from concurrent.futures import ThreadPoolExecutor

import requests

from .whitelist import doh_urls, resolve as doh_resolve

DNS = "bootstrap"
WARP_ASN = 13335
_SERVERS = 3          # сколько DoH-серверов группы пробовать на одно имя
_WORKERS = 8


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _resolve_one(host: str, urls: list, timeout: float) -> list[str] | None:
    for url in urls[:_SERVERS]:
        try:
            return sorted(set(doh_resolve(url, host, timeout, qtypes=(1,))))
        except (requests.RequestException, ValueError, IndexError, struct.error):
            continue                                 # следующий сервер группы
    return None


def resolve(hosts, singbox_config: str, timeout: float = 5.0) -> dict:
    """{хост: [IPv4]} для хостов, на которые DNS ответил. Сбои наружу не бросаются: хост
    без ответа (в том числе NXDOMAIN) попробуем в следующий раз."""
    hosts = [h for h in dict.fromkeys(hosts) if h]
    urls = list(dict.fromkeys(doh_urls(DNS, singbox_config)))   # NextDNS ×2 — один адрес
    with ThreadPoolExecutor(_WORKERS) as pool:
        answers = pool.map(lambda h: _resolve_one(h, urls, timeout), hosts)
    return {h: ips for h, ips in zip(hosts, answers) if ips}


def network(info: dict) -> str:
    """Сеть IP по сведениям ip-api: «AS13335 CLOUDFLARENET»."""
    asn = f"AS{info['asn']}" if info.get("asn") else ""
    return " ".join(part for part in (asn, info.get("as_name") or info.get("isp")) if part)


def chain(entry_ips, exit_ip: str, exit_cc: str, ip_info: dict) -> dict:
    """Вход и выход ноды для показа: entry_ip, entry_cc, entry_as, exit_ip, exit_cc, exit_as,
    chain, warp. Страна и сеть входа — первого IP входа со сведениями ip-api. Пусто, пока не
    известен вход или выход."""
    entry_ips = list(entry_ips or ())
    if not entry_ips or not exit_ip:
        return {}
    entry_info = next((ip_info[ip] for ip in entry_ips if (ip_info.get(ip) or {}).get("country")), {})
    exit_info = ip_info.get(exit_ip) or {}
    is_chain = exit_ip not in entry_ips
    return {"entry_ip": ", ".join(entry_ips), "entry_cc": entry_info.get("country", "").upper(),
            "entry_as": network(entry_info),
            "exit_ip": exit_ip, "exit_cc": (exit_cc or "").upper(), "exit_as": network(exit_info),
            "chain": 1 if is_chain else 0,
            "warp": 1 if is_chain and exit_info.get("asn") == WARP_ASN else 0}
