"""Разбор имени leaf-ноды `<Провайдер>-<Протокол>-<Страна>-out [CRC]`.

Общий модуль: переименователь собирает это имя, тестер разбирает обратно.

Примеры:
    "LUNA-vless(reality)-ru-out [78b59099]"
        provider="LUNA" protocol="vless(reality)" country="ru" node_id="78b59099"
    "hynet-ABC12-vless(grpc-reality)-se-out [aa11bb22]"
        provider="hynet-ABC12" protocol="vless(grpc-reality)" country="se" node_id="aa11bb22"

Тонкости: провайдер может содержать дефис ("hynet-ABC12"); протокол vless/vmess —
с транспортом+reality через дефис ВНУТРИ скобок ("vless(grpc-reality)"); страна —
чистый 2-буквенный код. Разбираем справа налево, уважая скобки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ID_RE = re.compile(r"\[([^\]]+)\]\s*$")


@dataclass
class NodeIdentity:
    raw: str
    provider: str
    protocol: str
    country: str
    node_id: str            # CRC из последних [..]
    label: str = ""         # доп. поле из [..] перед [CRC] (напр. AI), опционально

    def short(self) -> str:
        """Короткая метка для консоли: 'ru 78b59099' (+label)."""
        parts = [p for p in (self.country, self.label, self.node_id) if p]
        return " ".join(parts) or self.raw


def _split_provider_protocol(text: str) -> tuple[str, str]:
    """Разбить '<provider>-<protocol>' по последнему дефису ВНЕ скобок."""
    depth = 0
    cut = -1
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "-" and depth == 0:
            cut = i
    if cut == -1:
        return text, ""
    return text[:cut], text[cut + 1:]


def parse_node(tag: str) -> NodeIdentity:
    body = tag.strip()

    # Последние [..] — CRC; предыдущие [..] (если есть) — доп. поле (label).
    node_id = ""
    m = _ID_RE.search(body)
    if m:
        node_id = m.group(1).strip()
        body = body[: m.start()].strip()
    label = ""
    m2 = _ID_RE.search(body)
    if m2:
        label = m2.group(1).strip()
        body = body[: m2.start()].strip()

    if body.endswith("-out"):
        body = body[: -len("-out")]
    body = body.strip("-").strip()

    # Страна = последний токен; протокол = хвост до последнего дефиса ВНЕ скобок
    # (работает и для нового 'vless|grpc|reality', и для старого 'vless(grpc-reality)').
    if "-" in body:
        rest, country = body.rsplit("-", 1)
        provider, protocol = _split_provider_protocol(rest)
        if not protocol:
            provider, protocol = rest, ""
    else:
        provider, protocol, country = body, "", ""

    return NodeIdentity(raw=tag, provider=provider, protocol=protocol,
                        country=country, node_id=node_id, label=label)


def region_label(member_tag: str, group_tag: str) -> str:
    """Коарс-регион из тега под-селектора: 'eu-nodes-tester' + 'nodes-tester' -> 'eu'."""
    suffix = f"-{group_tag}"
    if member_tag.endswith(suffix):
        return member_tag[: -len(suffix)]
    return member_tag.split("-")[0]
