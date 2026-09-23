"""groups_params (v2) — переиспользуемый «рецепт» потока nodes_config.

    {
      "filters": { "exclude_types": [], "exclude_protocols": [], "exclude_countries": [],
                   "exclude_names": { "<provider>|*": ["подстрока", …] } },
      "rename":  { "labels": { "AI": ["ChatGPT", …] }, "domain_resolver_tag": "bootstrap" },
      "selector": {…}, "urltest": {…},
      "emit": { "nodes_tester": true, "global_failsafe": false, "ensure_regions": ["eu","us","other"] },
      "raw_user_nodes": false
    }

v1 (без filters/rename) принимается как есть — недостающее берётся из дефолтов.
Файла нет вовсе → все дефолты (как и прежде при отсутствии groups_params.json).
"""

import copy

from nodes_common.fileio import read_json

DEFAULTS = {
    "filters": {"exclude_types": [], "exclude_protocols": [], "exclude_countries": [],
                "exclude_names": {}},
    "rename": {"labels": {}, "domain_resolver_tag": "bootstrap"},
    "selector": {},
    "urltest": {},
    "emit": {},
    "raw_user_nodes": False,
}


class ParamsError(ValueError):
    """Некорректный groups_params."""


def _str_list(value, where):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ParamsError(f"{where}: ожидается список строк")
    return value


def load(data=None):
    """dict groups_params (v1/v2) или None → нормализованные параметры (глубокая копия)."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ParamsError("groups_params: ожидается JSON-объект")
    out = copy.deepcopy(DEFAULTS)
    for key in ("selector", "urltest", "emit"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ParamsError(f"groups_params.{key}: ожидается объект")
            out[key] = copy.deepcopy(data[key])
    out["raw_user_nodes"] = bool(data.get("raw_user_nodes", False))

    filters = data.get("filters") or {}
    if not isinstance(filters, dict):
        raise ParamsError("groups_params.filters: ожидается объект")
    for key in ("exclude_types", "exclude_protocols", "exclude_countries"):
        if key in filters:
            out["filters"][key] = _str_list(filters[key], f"filters.{key}")
    names = filters.get("exclude_names", {})
    if not isinstance(names, dict):
        raise ParamsError("filters.exclude_names: ожидается объект {провайдер|*: [подстроки]}")
    out["filters"]["exclude_names"] = {p: _str_list(v, f"filters.exclude_names.{p}")
                                       for p, v in names.items()}

    rename = data.get("rename") or {}
    if not isinstance(rename, dict):
        raise ParamsError("groups_params.rename: ожидается объект")
    labels = rename.get("labels", {})
    if not isinstance(labels, dict):
        raise ParamsError("rename.labels: ожидается объект {метка: [ключевые слова]}")
    out["rename"]["labels"] = {k: _str_list(v, f"rename.labels.{k}") for k, v in labels.items()}
    if rename.get("domain_resolver_tag"):
        out["rename"]["domain_resolver_tag"] = str(rename["domain_resolver_tag"])

    emit = out["emit"]
    if "ensure_regions" in emit:
        _str_list(emit["ensure_regions"], "emit.ensure_regions")
    return out


def load_path(path):
    """Файл groups_params или None (→ дефолты)."""
    return load(read_json(path) if path else None)
