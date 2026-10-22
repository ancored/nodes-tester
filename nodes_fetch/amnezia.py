"""Amnezia Premium: конфиги AmneziaWG по странам через шлюз Amnezia.

Подписка Amnezia Premium — ссылка vpn://: base64url от 4 байт длины и zlib-потока с JSON, в нём
`api_config` (тип сервиса, страна пользователя) и `auth_data.api_key` — секрет, которым
авторизуется каждый запрос. Шлюз принимает POST /v1/<метод> с телом
{key_payload, api_payload}: случайные ключ и вектор AES зашифрованы публичным ключом шлюза
(RSA, PKCS#1 v1.5), сам запрос — AES-256-CBC; ответ шифруется тем же ключом AES. Публичный
ключ шлюза в проект не входит: его извлекают из официального приложения AmneziaVPN.

Методы: account_info (только чтение), native_config (файл .conf страны: повторный выпуск
заменяет прежний файл этой страны, каждый файл занимает слот подписки), revoke_native_config.

Запросу из России шлюз выдаёт конфиг через relay Amnezia (в ответе routing_decision.relay.effective,
в файле — I1 и адрес relay), запросу из-за границы — прямой на сервер страны, а такой путь из России режется.
Поэтому шлюз нужен напрямую, без зарубежного прокси. Открытый HTTP к gw.amnezia.org провайдеры режут по
заголовку Host — имя резолвим сами и шлём запрос на IP.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import socket
import uuid
import zlib
from urllib.parse import urlsplit

import requests

from . import _aes
from .happ_crypto import _children, _tlv

GATEWAYS = ("http://gw.amnezia.org/",)
APP_VERSION = "5.0.3.0"                 # версия официального клиента, которой представляемся
_TIMEOUT = (15, 30)
_KEY_PLACEHOLDER = "$WIREGUARD_CLIENT_PRIVATE_KEY"


class AmneziaError(RuntimeError):
    """Шлюз отказал или ответил непонятно. status — http_status из ответа (если есть)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def decode_link(link: str) -> dict:
    """vpn://… → JSON подписки (api_config, auth_data)."""
    data = "".join(link.split())
    data = data[6:] if data.startswith("vpn://") else data
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        sub = json.loads(zlib.decompress(raw[4:]))
    except (binascii.Error, ValueError, zlib.error) as exc:
        raise AmneziaError("ссылка vpn:// не разбирается") from exc
    if not isinstance(sub, dict) or not (sub.get("auth_data") or {}).get("api_key"):
        raise AmneziaError("в ссылке нет api_key — это не подписка Amnezia Premium")
    return sub


# --- RSA и X25519 ---------------------------------------------------------------------

def rsa_public_key(pem: str) -> tuple[int, int]:
    """(n, e) из PEM SubjectPublicKeyInfo."""
    body = "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))
    try:
        der = base64.b64decode(body, validate=True)
        _, start, end = _tlv(der, 0)
        _, (tag, bits, stop) = list(_children(der, start, end))[:2]
        if tag != 0x03:
            raise ValueError
        key = der[bits + 1:stop]                          # BIT STRING без счётчика лишних бит
        _, start, end = _tlv(key, 0)
        n, e = (int.from_bytes(key[s:t], "big") for _, s, t in _children(key, start, end))
    except (binascii.Error, ValueError, IndexError) as exc:
        raise AmneziaError("ключ шлюза — не публичный ключ RSA в PEM") from exc
    return n, e


def rsa_encrypt(key: tuple[int, int], data: bytes) -> bytes:
    """PKCS#1 v1.5, тип 2: 00 02 PS(ненулевые) 00 данные."""
    n, e = key
    size = (n.bit_length() + 7) // 8
    if len(data) > size - 11:
        raise ValueError("данные длиннее блока RSA")
    pad = bytearray()
    while len(pad) < size - 3 - len(data):
        pad += bytes(b for b in os.urandom(size) if b)
    block = b"\x00\x02" + bytes(pad[:size - 3 - len(data)]) + b"\x00" + data
    return pow(int.from_bytes(block, "big"), e, n).to_bytes(size, "big")


_P = 2 ** 255 - 19


def x25519(scalar: bytes, point: bytes) -> bytes:
    """Умножение точки Curve25519 (RFC 7748)."""
    k = bytearray(scalar)
    k[0] &= 248
    k[31] = (k[31] & 127) | 64
    k = int.from_bytes(k, "little")
    x1 = int.from_bytes(point, "little") & (2 ** 255 - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (k >> t) & 1
        if swap ^ bit:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b, c, d = x2 + z2, x2 - z2, x3 + z3, x3 - z3
        aa, bb, da, cb = a * a, b * b, d * a, c * b
        e = aa - bb
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def wireguard_keypair() -> tuple[str, str]:
    """(закрытый, открытый) ключи WireGuard в base64."""
    private = bytearray(os.urandom(32))
    private[0] &= 248
    private[31] = (private[31] & 127) | 64
    public = x25519(bytes(private), (9).to_bytes(32, "little"))
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


# --- Шлюз -----------------------------------------------------------------------------

def _targets(url: str) -> list[str]:
    """Адреса для запроса к шлюзу: у открытого HTTP по имени — IP этого имени (по Host режут),
    https и IP — как есть (имя нужно для TLS)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "http" or not host:
        return [url]
    try:
        ipaddress.ip_address(host)
        return [url]
    except ValueError:
        pass
    port = parts.port or 80
    try:
        ips = dict.fromkeys(ai[4][0] for ai in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM))
    except OSError:
        return [url]
    suffix = f":{parts.port}" if parts.port else ""
    return [f"http://{ip}{suffix}{parts.path or '/'}" for ip in ips] or [url]


class Gateway:
    def __init__(self, link: str, gateway_key: str, proxies=None, gateways=GATEWAYS):
        self.sub = decode_link(link)
        self.key = rsa_public_key(gateway_key)
        self.proxies = proxies
        self.gateways = list(gateways)
        self.installation = str(uuid.uuid4())

    def _payload(self, **fields) -> dict:
        api = self.sub.get("api_config") or {}
        payload = {"os_version": "linux", "app_version": APP_VERSION, "cli_name": "AmneziaVPN",
                   "app_language": "en", "installation_uuid": self.installation,
                   "user_country_code": api.get("user_country_code"),
                   "service_type": api.get("service_type"), "auth_data": self.sub["auth_data"]}
        payload.update(fields)
        return {k: v for k, v in payload.items() if v not in (None, "")}

    def post(self, method: str, **fields) -> dict:
        key, iv = os.urandom(32), os.urandom(32)
        keys = json.dumps({"aes_key": base64.b64encode(key).decode(),
                           "aes_iv": base64.b64encode(iv).decode(),
                           "aes_salt": base64.b64encode(os.urandom(8)).decode()}).encode()
        body = {"key_payload": base64.b64encode(rsa_encrypt(self.key, keys)).decode(),
                "api_payload": base64.b64encode(_aes.encrypt_cbc(
                    json.dumps(self._payload(**fields)).encode(), key, iv)).decode()}
        errors = []
        targets = [target for url in self.gateways for target in _targets(url)]
        for base in targets:
            try:
                resp = requests.post(base.rstrip("/") + "/v1/" + method, json=body, timeout=_TIMEOUT,
                                     proxies=self.proxies, allow_redirects=False,
                                     headers={"X-Client-Request-ID": str(uuid.uuid4())})
                break
            except requests.RequestException as exc:
                errors.append(exc.__class__.__name__)
        else:
            raise AmneziaError(f"{method}: шлюз недоступен ({', '.join(errors) or 'нет адресов'})")
        try:
            data = json.loads(_aes.decrypt_cbc(resp.content, key, iv))
        except ValueError:
            try:                                  # ошибки шлюз отдаёт и открытым JSON
                data = json.loads(resp.content)
            except ValueError:
                data = {"http_status": resp.status_code, "message": "ответ не расшифрован"}
        if not isinstance(data, dict):
            raise AmneziaError(f"{method}: неожиданный ответ шлюза")
        status = data.get("http_status", resp.status_code)
        if not isinstance(status, int) or status >= 300:
            raise AmneziaError(f"{method}: HTTP {status} — {data.get('message') or 'без описания'}",
                               status)
        return data

    def account_info(self) -> dict:
        return self.post("account_info", cli_version=APP_VERSION, subscription_status="active")

    def issue(self, country: str, relay: bool = True) -> str:
        """Новый файл .conf страны (прежний файл этой страны перестаёт работать). relay — нужен
        конфиг через relay Amnezia: выданный без него сразу отзывается."""
        private, public = wireguard_keypair()
        data = self.post("native_config", server_country_code=country, service_protocol="awg",
                         public_key=public)
        conf = data.get("config")
        if not isinstance(conf, str) or _KEY_PLACEHOLDER not in conf:
            raise AmneziaError(f"native_config {country}: в ответе нет конфига с местом для ключа")
        routing = data.get("routing_decision") or {}
        if relay and (routing.get("relay") or {}).get("effective") is False:
            self.revoke(country)
            raise AmneziaError(f"native_config {country}: конфиг без relay (запрос не из России?) — отозван")
        return conf.replace(_KEY_PLACEHOLDER, private)

    def revoke(self, country: str) -> None:
        """Отозвать файл страны; 404 — его уже нет."""
        try:
            self.post("revoke_native_config", server_country_code=country, service_protocol="awg")
        except AmneziaError as exc:
            if exc.status != 404:
                raise
