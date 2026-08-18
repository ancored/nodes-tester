"""Отпечаток настроек ноды (CRC32) — общий для переименователя и тестера.

БАЙТ-В-БАЙТ как в исходном sing-box-subscribe/tool.py: переименователь пишет CRC
в тег, тестер парсит его обратно и сверяет. Любое изменение сериализации сдвинет
все CRC и «переименует» уже развёрнутые ноды — поэтому трогать нельзя.
"""

from __future__ import annotations

import json
import zlib


def node_payload(node: dict) -> str:
    """Каноничный JSON настроек ноды: всё кроме tag, domain_resolver и '_'-полей."""
    payload = {k: v for k, v in node.items()
               if k not in ("tag", "domain_resolver") and not k.startswith("_")}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_crc32(node: dict) -> str:
    """CRC32 (8 hex) от node_payload — отпечаток, меняется только при смене настроек."""
    return format(zlib.crc32(node_payload(node).encode("utf-8")) & 0xFFFFFFFF, "08x")
