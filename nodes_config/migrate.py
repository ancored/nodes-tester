"""Миграция конфигов v1 → v2.

v1: один providers.json (подписки + фильтры/переименование + save_config_path) и
groups_params.json (selector/urltest/emit/raw_user_nodes).
v2: providers.json — только загрузка (nodes_fetch); groups_params.json — всё про
фильтры/переименование/группы (nodes_config).

    python -m nodes_config migrate --from config/ --to config-v2/

golden-тест проверяет миграцию: v1-набор через split_v1 обязан дать прежний nodes.json.
"""

import copy
import os
import re
import shutil

from nodes_common.fileio import atomic_write_text, dumps_json, read_json

# Схема share-ссылки (v1 exclude_protocol) → тип sing-box outbound (v2 filters.exclude_types).
SCHEME_TYPES = {"ss": "shadowsocks", "ssr": "shadowsocksr", "hy2": "hysteria2",
                "wg": "wireguard", "wireguard": "wireguard", "socks5": "socks",
                "http2": "http", "https": "http"}
_SUB_DROP = ("ex-node-name", "prefix", "emoji", "subgroup")


def _split_list(value):
    return [x.strip() for x in re.split(r"[,|]", value or "") if x.strip()]


def split_v1(providers, groups_params=None):
    """(providers v1, groups_params v1|None) → (providers v2, groups_params v2, output_path).
    Уже v2-файлы проходят без потерь (лишних ключей нет — ничего не переносится)."""
    gp = copy.deepcopy(groups_params or {})
    filters = gp.setdefault("filters", {})
    rename = gp.setdefault("rename", {})

    types = [SCHEME_TYPES.get(s, s) for s in _split_list(providers.get("exclude_protocol"))]
    if types:
        filters["exclude_types"] = list(dict.fromkeys(filters.get("exclude_types", []) + types))
    if providers.get("exclude_node_protocols"):
        filters["exclude_protocols"] = list(providers["exclude_node_protocols"])
    if providers.get("exclude_countries"):
        filters["exclude_countries"] = list(providers["exclude_countries"])
    names = {}
    for sub in providers.get("subscribes", []):
        if sub.get("ex-node-name"):
            names[sub["tag"]] = _split_list(sub["ex-node-name"])
    if names:
        filters["exclude_names"] = dict(filters.get("exclude_names", {}), **names)
    if providers.get("labels"):
        rename["labels"] = copy.deepcopy(providers["labels"])
    if providers.get("domain_resolver_tag"):
        rename["domain_resolver_tag"] = providers["domain_resolver_tag"]
    if not filters:
        gp.pop("filters")
    if not rename:
        gp.pop("rename")

    subs = []
    for sub in providers.get("subscribes", []):
        s = {k: v for k, v in sub.items() if k not in _SUB_DROP}
        if "User-Agent" in s:
            s["user_agent"] = s.pop("User-Agent")
        subs.append(s)
    prov2 = {"subscribes": subs}
    if providers.get("fetch"):
        prov2["fetch"] = copy.deepcopy(providers["fetch"])
    return prov2, gp, providers.get("save_config_path")


def migrate_dir(src, dst, force=False, log=print):
    """Каталог v1 (providers.json, groups_params.json, user_nodes.json, папки folder-подписок)
    → каталог v2. Существующие файлы в dst без force не перезаписываются."""
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if src == dst and not force:
        raise ValueError("--to совпадает с --from: v1-файлы будут перезаписаны (нужен --force)")
    providers = read_json(os.path.join(src, "providers.json"))
    gp_path = os.path.join(src, "groups_params.json")
    groups_params = read_json(gp_path) if os.path.exists(gp_path) else None
    prov2, gp2, output = split_v1(providers, groups_params)

    targets = {"providers.json": dumps_json(prov2), "groups_params.json": dumps_json(gp2)}
    for name in targets:
        if os.path.exists(os.path.join(dst, name)) and not force:
            raise FileExistsError(f"{os.path.join(dst, name)} уже есть (нужен --force)")
    for name, text in targets.items():
        atomic_write_text(os.path.join(dst, name), text)
        log(f"[migrate] записан {os.path.join(dst, name)}")
    if src != dst:
        un = os.path.join(src, "user_nodes.json")
        if os.path.exists(un):
            shutil.copy2(un, os.path.join(dst, "user_nodes.json"))
            log(f"[migrate] скопирован user_nodes.json")
        for sub in prov2["subscribes"]:            # относительные папки/файлы подписок
            rel = sub.get("path") if sub.get("type") == "folder" else sub.get("file")
            if rel and not os.path.isabs(rel) and os.path.exists(os.path.join(src, rel)):
                s, d = os.path.join(src, rel), os.path.join(dst, rel)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(s, d)
                log(f"[migrate] скопирован {rel}")
    if output:
        log(f"[migrate] выход v1 (save_config_path) был: {output} — теперь это -o nodes_config "
            f"или output потока в pipeline.json")
    return prov2, gp2, output
