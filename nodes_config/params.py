"""groups_params (v2) — переиспользуемый «рецепт» потока nodes_config.

    {
      "filters": { "exclude_types": [], "exclude_protocols": [], "exclude_countries": [],
                   "exclude_names": { "<provider>|*": ["подстрока", …] } },
      "rename":  { "labels": { "AI": ["ChatGPT", …] }, "domain_resolver_tag": "bootstrap" },
      "selector": {…}, "urltest": {…},
      "emit": { "nodes_tester": true, "global_failsafe": false, "ensure_regions": ["eu","us","other"] },
      "regions": { "eu": ["de", "nl", …], "asia": ["jp", "sg"] },
      "groups": [ { "name": "eu", "enabled": true, "in_global": true, "fallback": true,
                    "match": { "regions": ["eu"] } }, … ],
      "raw_user_nodes": false
    }

groups не задан → прежняя схема (eu/us/ru/other, обязательные — emit.ensure_regions).

v1 (без filters/rename) принимается как есть — недостающее берётся из дефолтов.
Файла нет вовсе → все дефолты (как и прежде при отсутствии groups_params.json).
"""

import copy
import re

from nodes_common.fileio import read_json

DEFAULTS = {
    "filters": {"exclude_types": [], "exclude_protocols": [], "exclude_countries": [],
                "exclude_names": {}},
    "rename": {"labels": {}, "domain_resolver_tag": "bootstrap"},
    "selector": {},
    "urltest": {},
    "emit": {},
    "regions": {},
    "groups": None,
    "raw_user_nodes": False,
}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_MATCH_KEYS = ("countries", "regions", "labels",
               "exclude_countries", "exclude_regions", "exclude_labels")


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

    regions = data.get("regions") or {}
    if not isinstance(regions, dict):
        raise ParamsError("groups_params.regions: ожидается объект {регион: [страны]}")
    for name, ccs in regions.items():
        if not _NAME_RE.match(str(name)):
            raise ParamsError(f"regions: недопустимое имя региона {name!r}")
        _str_list(ccs, f"regions.{name}")
    out["regions"] = {name: [c.lower() for c in ccs] for name, ccs in regions.items()}

    if "groups" in data and data["groups"] is not None:
        out["groups"] = _load_groups(data["groups"], set(out["regions"]))
    return out


def _load_groups(groups, custom_regions):
    if not isinstance(groups, list):
        raise ParamsError("groups_params.groups: ожидается список групп")
    known_regions = {"eu", "us", "ru", "other"} | custom_regions
    seen, out = set(), []
    for i, g in enumerate(groups):
        where = f"groups[{i}]"
        if not isinstance(g, dict):
            raise ParamsError(f"{where}: ожидается объект")
        name = g.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name) or name in ("global", "nodes"):
            raise ParamsError(f"{where}.name: латиница в нижнем регистре, цифры и _; "
                              f"не global/nodes (получено {name!r})")
        if name in seen:
            raise ParamsError(f"{where}.name: группа '{name}' повторяется")
        seen.add(name)
        for flag in ("enabled", "in_global", "fallback"):
            if flag in g and not isinstance(g[flag], bool):
                raise ParamsError(f"{where}.{flag}: ожидается true/false")
        match = g.get("match") or {}
        if not isinstance(match, dict) or set(match) - set(_MATCH_KEYS):
            raise ParamsError(f"{where}.match: допустимые ключи — {', '.join(_MATCH_KEYS)}")
        for key, values in match.items():
            _str_list(values, f"{where}.match.{key}")
            if key.endswith("regions"):
                unknown = set(values) - known_regions
                if unknown:
                    raise ParamsError(f"{where}.match.{key}: неизвестные регионы "
                                      f"{', '.join(sorted(unknown))}")
        out.append({"name": name, "enabled": g.get("enabled", True),
                    "in_global": g.get("in_global", True), "fallback": g.get("fallback", True),
                    "match": copy.deepcopy(match)})
    return out


def load_path(path):
    """Файл groups_params или None (→ дефолты)."""
    return load(read_json(path) if path else None)
