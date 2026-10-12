"""Отпечатки ноды: CRC32 настроек сервера и CRC32 учётных данных.

Outbound делится на две части:
  настройки — тип, адрес, порт, транспорт, TLS/SNI, ключ Reality и т. п.: «сервер и способ
              подключения»; их CRC — `node_crc`;
  учётные данные — то, что выдаёт провайдер конкретному аккаунту: uuid, пароль, short_id
              Reality, ключи WireGuard; их CRC — `cred_crc`.
Ключ доступа `node_crc.cred_crc` стоит в теге в квадратных скобках и связывает ноду в
сборке, тестере, БД и админке. Провайдер перевыпустил учётные данные — меняется только
`cred_crc`, а страна, проверки и рейтинг ноды остаются у `node_crc`.
"""

from __future__ import annotations

import copy
import json
import zlib

# Учётные данные: поля верхнего уровня outbound. `address` — только у WireGuard: адрес
# клиента в туннеле (адрес сервера там лежит в peers[].address и остаётся в настройках).
_CRED_KEYS = ("uuid", "password", "auth_str", "username", "user",
              "private_key", "private_key_passphrase", "address")


def node_payload(node: dict) -> dict:
    """Outbound без tag, domain_resolver и служебных `_`-полей — то, что описывает ноду."""
    return {k: v for k, v in node.items()
            if k not in ("tag", "domain_resolver") and not k.startswith("_")}


def credential_paths(node: dict) -> list[list]:
    """Пути учётных данных в outbound: [["uuid"], ["tls", "reality", "short_id"], …].
    Пароль obfs у hysteria2 общий для сервера — он в настройках, не здесь."""
    paths = [[key] for key in _CRED_KEYS if key in node]
    tls = node.get("tls")
    reality = tls.get("reality") if isinstance(tls, dict) else None
    if isinstance(reality, dict) and "short_id" in reality:
        paths.append(["tls", "reality", "short_id"])
    for i, peer in enumerate(node.get("peers") or []):
        if isinstance(peer, dict) and "pre_shared_key" in peer:
            paths.append(["peers", i, "pre_shared_key"])
    return paths


def split_payload(node: dict) -> tuple[dict, dict]:
    """(настройки, учётные данные) ноды. Учётные данные — плоский словарь путь → значение."""
    settings = copy.deepcopy(node_payload(node))
    creds = {}
    for path in credential_paths(settings):
        parent = settings
        for step in path[:-1]:
            parent = parent[step]
        creds[".".join(map(str, path))] = parent.pop(path[-1])
    return settings, creds


def canonical(value) -> str:
    """Каноничный JSON — ровно то, что хешируется."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _crc32(value) -> str:
    return format(zlib.crc32(canonical(value).encode("utf-8")) & 0xFFFFFFFF, "08x")


def node_key(node: dict) -> str:
    """Ключ доступа `node_crc.cred_crc` (8 + 8 hex)."""
    settings, creds = split_payload(node)
    return f"{_crc32(settings)}.{_crc32(creds)}"
