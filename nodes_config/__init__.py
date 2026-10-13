"""Стадия nodes_config конвейера: raw_nodes.json + config.json → nodes.json.

Фильтры, переименование по схеме naming, CRC, dedupe и selector/urltest-группы.
CLI — `python -m nodes_config`, API — nodes_config.build / params.
"""
