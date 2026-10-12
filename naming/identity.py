"""Разбор имени leaf-ноды `<Провайдер>-<Протокол>-out [node_crc.cred_crc]`.

Общий модуль: сборка пишет это имя, тестер и админка разбирают обратно.

Примеры:
    "LUNA-vless|reality-out [5823d193.0be1f7a2]"
        provider="LUNA" protocol="vless|reality" node_id="5823d193.0be1f7a2"
    "hynet-ABC12-vless|grpc|reality-out [aa11bb22.0c0ffee0]"
        provider="hynet-ABC12" protocol="vless|grpc|reality"

Провайдер может содержать дефис ("hynet-ABC12"), протокол — нет (токены через '|'),
поэтому протокол — хвост после последнего дефиса. Страны в имени нет: её измеряет тестер.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ID_RE = re.compile(r"\[([^\]]+)\]\s*$")
# Ключ доступа: CRC настроек сервера и CRC учётных данных, по 8 hex.
NODE_KEY_RE = re.compile(r"[0-9a-f]{8}\.[0-9a-f]{8}")


@dataclass
class NodeIdentity:
    raw: str
    provider: str
    protocol: str
    node_id: str            # ключ доступа из [..]: node_crc.cred_crc

    def short(self) -> str:
        """Короткая метка для консоли: 'LUNA 5823d193.0be1f7a2'."""
        return " ".join(p for p in (self.provider, self.node_id) if p) or self.raw

    @property
    def node_crc(self) -> str:
        """CRC настроек сервера — общий у всех доступов одной ноды."""
        return self.node_id.partition(".")[0]

    @property
    def cred_crc(self) -> str:
        """CRC учётных данных доступа."""
        return self.node_id.partition(".")[2]


def parse_node(tag: str) -> NodeIdentity:
    body = tag.strip()
    node_id = ""
    m = _ID_RE.search(body)
    if m:
        node_id = m.group(1).strip()
        body = body[: m.start()].strip()
    if body.endswith("-out"):
        body = body[: -len("-out")]
    provider, _, protocol = body.strip("-").strip().rpartition("-")
    if not provider:
        provider, protocol = protocol, ""
    return NodeIdentity(raw=tag, provider=provider, protocol=protocol, node_id=node_id)


# Коарс-регионы (префикс имени группы) и токены протоколов (хвост имени группы).
_COARSE_REGIONS = {"eu", "us", "ru", "other"}
_PROTO_TOKENS = {
    "vless", "vmess", "trojan", "ss", "shadowsocks", "tuic",
    "hysteria", "hysteria2", "hy2", "wireguard", "wg", "anytls", "http", "socks",
}


def parse_group(tag: str) -> tuple[str, str] | None:
    """Разбор имени группы-аутбаунда `{region}-{provider}[-{proto}]-out[-failsafe]`.

    Возвращает (provider, region) для провайдер-группы; иначе None —
    для auto/global-групп, direct и leaf-нод (провайдера в них не выделить).

    Примеры:
        'eu-LUNA-vless|reality-out-failsafe' -> ('LUNA', 'eu')
        'ru-LUNA-out-failsafe'               -> ('LUNA', 'ru')
        'eu-hynet-XYZ89-vless|reality-out'   -> ('hynet-XYZ89', 'eu')
        'eu-auto-out-failsafe'               -> None
        'global-auto-out' / 'direct-out'     -> None
    """
    body = tag.strip()
    if body.endswith("-failsafe"):
        body = body[: -len("-failsafe")]
    if body.endswith("-out"):
        body = body[: -len("-out")]
    else:
        return None                       # не групповой аутбаунд

    region, _, rest = body.partition("-")
    if region not in _COARSE_REGIONS:
        return None                       # global / прочее — не провайдер-группа
    if not rest or rest == "auto":
        return None                       # auto-группа — провайдера нет

    # rest = provider[-proto]; отделяем proto (последний сегмент с '|' или из набора).
    if "-" in rest:
        head, _, last = rest.rpartition("-")
        if "|" in last or last.lower() in _PROTO_TOKENS:
            return (head, region)
    return (rest, region)


def region_label(member_tag: str, group_tag: str) -> str:
    """Коарс-регион из тега под-селектора: 'eu-nodes-tester' + 'nodes-tester' -> 'eu'."""
    suffix = f"-{group_tag}"
    if member_tag.endswith(suffix):
        return member_tag[: -len(suffix)]
    return member_tag.split("-")[0]
