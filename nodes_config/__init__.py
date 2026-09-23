"""Стадия nodes_config конвейера: raw_nodes.json (+ groups_params, + user_nodes) → nodes.json.

Фильтры, переименование по схеме naming, CRC, dedupe и selector/urltest-группы.
CLI — `python -m nodes_config` (+ `migrate`), API — nodes_config.build / params / migrate.
"""
