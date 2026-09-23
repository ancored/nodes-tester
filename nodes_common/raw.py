"""Контракт raw_nodes.json (v1) — выход nodes_fetch, вход nodes_config.

    {
      "version": 1,
      "generated_at": "2026-09-23T12:00:00Z",
      "providers": "providers-main",
      "nodes_hash": "sha256:…",
      "sources": [ {provider, kind, ok, count, fetched_at, last_ok_at, stale, error} ],
      "nodes":   [ {provider, title, cc_hint, outbound} ]
    }

- outbound — sing-box outbound/endpoint как выдал парсер, БЕЗ tag и без '_'-полей;
  исходное имя ноды из подписки — в title (его используют rename/labels, на него же
  ссылаются detour соседних нод той же подписки);
- cc_hint — подсказка страны, когда её нет в названии (AWG: из имени файла), иначе null;
- порядок nodes = порядок подписок (по первому появлению провайдера) + порядок в подписке;
- nodes_hash — sha256 каноничного JSON секции nodes: меняется только при смене нод.
Схема для IDE/админки — schemas/raw_nodes.schema.json.
"""

import hashlib
import json

from .fileio import read_json

RAW_VERSION = 1
SOURCE_KINDS = ("url", "file", "folder", "happ")


class RawFormatError(ValueError):
    pass


def nodes_hash(nodes):
    canon = json.dumps(nodes, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def make_node(provider, title, outbound, cc_hint=None):
    """Нода raw из узла парсера: tag → title, '_'-поля отбрасываются."""
    return {"provider": provider, "title": title, "cc_hint": cc_hint or None,
            "outbound": {k: v for k, v in outbound.items()
                         if k != "tag" and not k.startswith("_")}}


def validate(raw):
    """Структурная проверка raw (без jsonschema — работает на голом роутере)."""
    if not isinstance(raw, dict):
        raise RawFormatError("raw_nodes: ожидается JSON-объект")
    if raw.get("version") != RAW_VERSION:
        raise RawFormatError(f"raw_nodes: неподдерживаемая версия {raw.get('version')!r} "
                             f"(ожидается {RAW_VERSION})")
    for key in ("sources", "nodes"):
        if not isinstance(raw.get(key), list):
            raise RawFormatError(f"raw_nodes: поле {key!r} должно быть списком")
    for i, node in enumerate(raw["nodes"]):
        if not isinstance(node, dict) or not isinstance(node.get("outbound"), dict):
            raise RawFormatError(f"raw_nodes: nodes[{i}] без объекта outbound")
        if not isinstance(node.get("provider"), str) or not node["provider"]:
            raise RawFormatError(f"raw_nodes: nodes[{i}] без provider")
        if not isinstance(node.get("title"), str):
            raise RawFormatError(f"raw_nodes: nodes[{i}] без title")
        if "type" not in node["outbound"]:
            raise RawFormatError(f"raw_nodes: nodes[{i}].outbound без type")
        if "tag" in node["outbound"]:
            raise RawFormatError(f"raw_nodes: nodes[{i}].outbound не должен содержать tag")
    return raw


def load(path):
    return validate(read_json(path))
