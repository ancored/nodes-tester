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
