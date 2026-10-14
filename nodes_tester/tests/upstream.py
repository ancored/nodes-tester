"""Проходит ли через ноду вышестоящий прокси: outbound sing-box, который выходит через группу
(`detour`), например SOCKS со статическим IP. Нода бывает жива, но не пускает соединения на
порт прокси, или сам прокси не принимает её IP — тогда выход через группу мёртв, хотя
остальные проверки ноду пропускают.

Через ноду (SOCKS тестера) открывается соединение с сервером outbound, проходит его
рукопожатие SOCKS5 (логин и пароль — из конфига sing-box) и через него запрашивается url.
Нода проходит, если ответ 200 получен во всех attempts попытках. Outbound не найден или не
SOCKS — результат неопределённый, прошлый вердикт сохраняется.

Тест обязательный (groups[].require.upstream), в рейтинг не входит.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import struct
from urllib.parse import unquote, urlsplit

import requests.certs

from .base import BaseTest, TestContext, TestResult, register


def _outbound(path: str, tag: str) -> "dict | None":
    try:
        with open(path, encoding="utf-8") as fh:
            outbounds = json.load(fh).get("outbounds") or []
    except (OSError, ValueError):
        return None
    return next((o for o in outbounds if o.get("tag") == tag), None)


def _recv(sock, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("прокси закрыл соединение")
        data += chunk
    return data


def socks5_connect(sock, host: str, port: int, user: str = "", password: str = "") -> None:
    """Рукопожатие SOCKS5 и CONNECT host:port поверх уже открытого соединения."""
    method = b"\x02" if user else b"\x00"
    sock.sendall(b"\x05\x01" + method)
    reply = _recv(sock, 2)
    if reply != b"\x05" + method:
        raise OSError(f"прокси отклонил метод входа ({reply.hex()})")
    if user:
        login, secret = user.encode(), password.encode()
        sock.sendall(b"\x01" + bytes([len(login)]) + login + bytes([len(secret)]) + secret)
        if _recv(sock, 2)[1] != 0:
            raise OSError("прокси отклонил логин")
    try:
        ip = ipaddress.ip_address(host)
        addr = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
    except ValueError:
        name = host.encode("idna")
        addr = b"\x03" + bytes([len(name)]) + name
    sock.sendall(b"\x05\x01\x00" + addr + struct.pack(">H", port))
    head = _recv(sock, 4)
    if head[1] != 0:
        raise OSError(f"прокси отклонил CONNECT (код {head[1]})")
    addr_len = {1: 4, 4: 16}.get(head[3]) or _recv(sock, 1)[0]
    _recv(sock, addr_len + 2)


def http_get(sock, url: str) -> tuple[int, bytes]:
    """GET url поверх открытого соединения: код ответа и тело (до 64 КБ)."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        context = ssl.create_default_context(cafile=requests.certs.where())
        sock = context.wrap_socket(sock, server_hostname=parts.hostname)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}\r\nUser-Agent: curl/8.5.0\r\n"
                 f"Connection: close\r\n\r\n".encode())
    data = b""
    while len(data) < 65536:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].split()
    code = int(status[1]) if len(status) > 1 and status[1].isdigit() else 0
    return code, body


def exit_ip(body: bytes) -> str:
    """IP выхода из ответа: строка ip= (cdn-cgi/trace Cloudflare) или тело целиком."""
    text = body.decode("utf-8", "replace").strip()
    for line in text.splitlines():
        value = line[3:] if line.startswith("ip=") else line
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            continue
    return ""


@register
class UpstreamTest(BaseTest):
    name = "upstream"
    default_url = "https://www.cloudflare.com/cdn-cgi/trace"

    def run(self, ctx: TestContext) -> TestResult:
        url = self.url_for(ctx)
        tag = self.options.get("outbound") or ""
        ob = _outbound(self.options.get("singbox_config") or "", tag)
        if not ob or ob.get("type") != "socks":
            return TestResult(self.name, False, {"inconclusive": True},
                              error=f"outbound {tag!r} не найден в конфиге sing-box или не socks", url=url)
        tester = urlsplit(ctx.session.proxies["https"])
        target = urlsplit(url)
        attempts = max(1, int(self.options.get("attempts", 2)))
        errors, ips = [], set()
        for _ in range(attempts):
            try:
                with socket.create_connection((tester.hostname, tester.port), self.timeout(ctx)) as sock:
                    socks5_connect(sock, ob["server"], int(ob["server_port"]),
                                   unquote(tester.username or ""), unquote(tester.password or ""))
                    socks5_connect(sock, target.hostname,
                                   target.port or (443 if target.scheme == "https" else 80),
                                   ob.get("username") or "", ob.get("password") or "")
                    code, body = http_get(sock, url)
            except OSError as exc:              # ssl и тайм-ауты — наследники OSError
                errors.append(str(exc) or type(exc).__name__)
                continue
            if code != 200:
                errors.append(f"HTTP {code}")
            elif ip := exit_ip(body):
                ips.add(ip)
        metrics = {"attempts": attempts, "outbound": tag}
        if ips:
            metrics["exit_ip"] = ", ".join(sorted(ips))
        if errors:
            return TestResult(self.name, False, metrics, error="; ".join(sorted(set(errors))), url=url)
        return TestResult(self.name, True, metrics, url=url)
