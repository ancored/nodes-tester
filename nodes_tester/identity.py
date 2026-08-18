"""Ре-экспорт общего модуля имён (naming) для совместимости импортов тестера.

Единый источник — пакет `naming` в корне репозитория (общий с subscribe).
"""

from naming.identity import NodeIdentity, parse_node, region_label  # noqa: F401
from naming.regions import coarse_region  # noqa: F401
