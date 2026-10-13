"""Единая система имён нод sing-box: идентичность, протокол, ключ ноды.

Общий модуль для тестера (nodes_tester) и переименователя (subscribe) — чтобы
тег, сгенерированный при переименовании, и тег, разобранный тестером, всегда были
согласованы, а CRC совпадал.
"""

from .crc import canonical, credential_paths, node_key, node_payload, split_payload
from .identity import NODE_KEY_RE, NodeIdentity, parse_node
from .protocol import has_reality, node_protocol

__all__ = [
    "NODE_KEY_RE", "NodeIdentity", "parse_node",
    "node_protocol", "has_reality",
    "node_payload", "split_payload", "credential_paths", "canonical", "node_key",
]
