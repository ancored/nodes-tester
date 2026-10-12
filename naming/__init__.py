"""Единая система имён нод sing-box: идентичность, протокол, CRC, страны/регионы.

Общий модуль для тестера (nodes_tester) и переименователя (subscribe) — чтобы
тег, сгенерированный при переименовании, и тег, разобранный тестером, всегда были
согласованы, а CRC совпадал.
"""

from .crc import canonical, credential_paths, node_key, node_payload, split_payload
from .identity import NODE_KEY_RE, NodeIdentity, parse_group, parse_node, region_label
from .protocol import has_reality, node_protocol
from .regions import coarse_region, country_from_text, flag_to_code

__all__ = [
    "NODE_KEY_RE", "NodeIdentity", "parse_node", "parse_group", "region_label",
    "node_protocol", "has_reality",
    "node_payload", "split_payload", "credential_paths", "canonical", "node_key",
    "coarse_region", "country_from_text", "flag_to_code",
]
