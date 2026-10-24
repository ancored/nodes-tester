"""Проверка «белый список»: пропустит ли ноду режим белых списков мобильного интернета РФ.

Нода проходит, если адрес её сервера — в IP-списке и SNI — в списке доменов (домен и все его
поддомены); у нод без TLS (Shadowsocks, WireGuard, AWG, SOCKS) достаточно IP. Списки —
текстовые файлы ancored/ru-mobile-whitelist. Проверка локальная: трафик через ноду не идёт.

Домен сервера резолвится DoH-запросом к DNS-серверу из конфига sing-box (по тегу, например
dns-ru; у группы — к её серверам по очереди) или по URL: системный резолвер роутера
отправил бы имя ноды провайдеру.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import struct
import time

import requests

_FALLBACK_DOH = "https://1.1.1.1/dns-query"


# --- DNS поверх HTTPS (RFC 8484, wire format) --------------------------------

def _dns_query(name: str, qtype: int) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode("idna") for part in name.rstrip(".").split("."))
    return struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", qtype, 1)


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        length = data[pos]
        if length & 0xC0 == 0xC0:          # ссылка на имя выше — два байта
            return pos + 2
        if length == 0:
            return pos + 1
        pos += 1 + length


def _dns_addresses(data: bytes) -> list[str]:
    """Адреса A и AAAA из ответа DNS (без CNAME: цепочку раскрывает сервер)."""
    _, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if flags & 0x000F:
        raise ValueError(f"DNS rcode {flags & 0x000F}")
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(data, pos) + 4
    out = []
    for _ in range(ancount):
        pos = _skip_name(data, pos)
        rtype, _cls, _ttl, length = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype in (1, 28):
            out.append(str(ipaddress.ip_address(data[pos:pos + length])))
        pos += length
    return out


def resolve(url: str, name: str, timeout: float, qtypes=(1, 28)) -> list[str]:
    """Адреса имени через DoH-сервер url: IPv4 (тип 1) и IPv6 (28)."""
    out = []
    for qtype in qtypes:
        query = base64.urlsafe_b64encode(_dns_query(name, qtype)).rstrip(b"=").decode()
        resp = requests.get(url, params={"dns": query}, timeout=timeout,
                            headers={"Accept": "application/dns-message"})
        resp.raise_for_status()
        out += _dns_addresses(resp.content)
    return out


def doh_urls(spec: str, singbox_config: str) -> list[str]:
    """URL DoH-серверов: сам spec, если это URL, иначе сервер type https с этим тегом из
    конфига sing-box; группа (type group) — её серверы https по порядку. Сервер на IP с
    tls.server_name адресуется по этому имени: сертификат NextDNS на IP не выписан. Не нашлось —
    Cloudflare (Quad9 требует HTTP/2, а requests умеет только HTTP/1.1)."""
    if spec.startswith("https://"):
        return [spec]
    try:
        with open(singbox_config, encoding="utf-8") as fh:
            servers = (json.load(fh).get("dns") or {}).get("servers") or []
    except (OSError, ValueError):
        servers = []
    by_tag = {s.get("tag"): s for s in servers}
    server = by_tag.get(spec) or {}
    if server.get("type") == "group":
        members = [by_tag.get(tag) or {} for tag in server.get("servers") or []]
    else:
        members = [server]
    urls = []
    for s in members:
        if s.get("type") == "https":
            host = (s.get("tls") or {}).get("server_name") or s["server"]
            port = f":{s['server_port']}" if s.get("server_port") else ""
            urls.append(f"https://{host}{port}{s.get('path') or '/dns-query'}")
    return urls or [_FALLBACK_DOH]


# --- Списки ------------------------------------------------------------------

class Lists:
    """IP-подсети (поиск по маскам каждой длины префикса) и домены (domain_suffix)."""

    def __init__(self, ip_text: str, sni_text: str):
        self._nets: dict[tuple[int, int], set[int]] = {}       # (версия, префикс) → адреса сетей
        for line in ip_text.split():
            try:
                net = ipaddress.ip_network(line.strip(), strict=False)
            except ValueError:
                continue
            self._nets.setdefault((net.version, net.prefixlen), set()).add(int(net.network_address))
        self.domains = {line.strip().lower().rstrip(".") for line in sni_text.split() if line.strip()}

    def ip_in(self, ip: str) -> bool:
        addr = ipaddress.ip_address(ip)
        bits = addr.max_prefixlen
        value = int(addr)
        return any(value >> (bits - plen) << (bits - plen) in nets
                   for (version, plen), nets in self._nets.items() if version == addr.version)

    def sni_in(self, name: str) -> bool:
        parts = name.lower().rstrip(".").split(".")
        return any(".".join(parts[i:]) in self.domains for i in range(len(parts)))


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def server_and_sni(ob: dict) -> tuple[str, str]:
    """Адрес сервера ноды и SNI (пусто — TLS нет)."""
    if ob.get("type") == "wireguard":
        peers = ob.get("peers") or [{}]
        return str(peers[0].get("address") or ""), ""
    server = str(ob.get("server") or "")
    tls = ob.get("tls") or {}
    if not tls.get("enabled"):
        return server, ""
    return server, str(tls.get("server_name") or ("" if _is_ip(server) else server))


class WhitelistCheck:
    """Проверка с кэшем списков и адресов на срок ttl."""

    def __init__(self, params: dict, singbox_config: str, get=requests.get, clock=time.time):
        self.params = params
        self.singbox_config = singbox_config
        self._get, self._clock = get, clock
        self._lists: Lists | None = None
        self._lists_ts = 0.0
        self._resolved: dict[str, tuple[float, list[str]]] = {}

    def _load_lists(self) -> "Lists | None":
        if self._lists is None or self._clock() - self._lists_ts >= self.params["ttl"]:
            try:
                texts = []
                for key in ("ip_list", "sni_list"):
                    resp = self._get(self.params[key], timeout=self.params["timeout"])
                    resp.raise_for_status()
                    texts.append(resp.text)
                self._lists, self._lists_ts = Lists(*texts), self._clock()
            except requests.RequestException as exc:
                print(f"  [whitelist] списки не загружены: {exc}")      # остаются прежние
        return self._lists

    def _addresses(self, server: str) -> list[str]:
        if _is_ip(server):
            return [server]
        cached = self._resolved.get(server)
        if cached and self._clock() - cached[0] < self.params["ttl"]:
            return cached[1]
        for url in doh_urls(self.params["dns"], self.singbox_config):
            try:
                ips = resolve(url, server, self.params["timeout"])
                break
            except (requests.RequestException, ValueError, IndexError, struct.error) as exc:
                error = exc                         # следующий сервер группы
        else:
            raise error
        self._resolved[server] = (self._clock(), ips)
        return ips

    def run(self, ob: dict) -> dict:
        """Результат в формате тестов: ok; inconclusive — проверить не удалось (нет списков,
        не разрешилось имя): прежний вердикт ноды сохраняется."""
        lists = self._load_lists()
        if lists is None:
            return {"ok": False, "inconclusive": True, "error": "списки белых списков не загружены"}
        server, sni = server_and_sni(ob)
        try:
            ips = self._addresses(server)
        except (requests.RequestException, ValueError, IndexError, struct.error) as exc:
            return {"ok": False, "inconclusive": True, "server": server, "error": f"резолв {server}: {exc}"}
        if not ips:
            return {"ok": False, "inconclusive": True, "server": server, "error": f"у {server} нет адресов"}
        ip_ok = all(lists.ip_in(ip) for ip in ips)
        sni_ok = lists.sni_in(sni) if sni else None
        return {"ok": ip_ok and sni_ok is not False, "server": server, "ips": ips, "ip_ok": ip_ok,
                "sni": sni, "sni_ok": sni_ok}
