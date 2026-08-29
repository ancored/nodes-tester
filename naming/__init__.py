"""Единая система имён нод sing-box: идентичность, протокол, CRC, страны/регионы.

Общий модуль для тестера (nodes_tester) и переименователя (subscribe) — чтобы
тег, сгенерированный при переименовании, и тег, разобранный тестером, всегда были
согласованы, а CRC совпадал.
"""

from .crc import content_crc32, node_payload
from .identity import NodeIdentity, parse_group, parse_node, region_label
from .protocol import has_reality, node_protocol
from .regions import coarse_region, country_from_text, flag_to_code

__all__ = [
    "NodeIdentity", "parse_node", "parse_group", "region_label",
    "node_protocol", "has_reality",
    "node_payload", "content_crc32",
    "coarse_region", "country_from_text", "flag_to_code",
]
