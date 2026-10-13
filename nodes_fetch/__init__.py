"""Стадия fetch конвейера: подписки (config.json, раздел subscriptions) → raw_nodes.json.

Не зависит ни от naming, ни от nodes_config: только достаёт и парсит ноды.
CLI — `python -m nodes_fetch`, API — nodes_fetch.fetch (load_providers / run).
"""
