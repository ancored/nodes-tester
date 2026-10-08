"""Пресеты правил sing-box: отдельные JSON-файлы, склеиваемые с базой и нодами.

Пресет — фрагмент конфига sing-box (любые разделы) плюс метаданные в ключе `_preset`:

    {
      "_preset": {"title": "RU", "description": "…", "enabled": true, "priority": 300,
                  "priorities": {"dns.rules": 150}, "requires": {"groups": ["us"]}},
      "dns":   {"servers": […], "rules": […]},
      "route": {"rules": […], "rule_set": […]}
    }

Итоговый конфиг склеивает nodes_admin.merge: база выше всех, затем ноды, затем включённые
пресеты по `priority` (меньше — выше, при равенстве — по имени файла). `priorities`
переопределяет приоритет для отдельного пути: так DNS-правила пресета встают в другое место,
чем его правила маршрута. Внутри пресета порядок элементов не меняется. Одинаковые объекты
с тегом схлопываются, поэтому пресет сам объявляет нужные ему rule_set и DNS-серверы.

`requires.groups` — группы нод ({name}-auto-out), без которых пресет ссылается на
несуществующий outbound; окончательную проверку делает `sing-box check`.

Клиентская ветка склеивает так же базу клиента, whnodes.json и пресеты `clients/presets/`.
Клиенту локальные наборы правил роутера недоступны, поэтому `local` с путём в
/etc/sing-box/rules/ превращается в `remote` с адресом раздачи из `clients/settings.json`:

    {"rules_url": "https://<router-domain>:8443/<секретный путь>/", "update_interval": "24h"}

Командная строка (для apply-nodes.sh и build-clients.sh):

    python3 -m nodes_admin.presets assemble --base B --nodes N [--dir DIR] [--client-settings S] --out OUT
    python3 -m nodes_admin.presets list --dir DIR [--nodes nodes.json]  # состояние (JSON)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from nodes_admin.merge import Source, dumps, merge

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_PRIORITY = 500
META_KEYS = {"title", "description", "enabled", "priority", "priorities", "requires"}
RULES_DIR = "/etc/sing-box/rules/"
CLIENT_SETTINGS_KEYS = {"rules_url", "update_interval"}


class PresetError(ValueError):
    pass


def validate(name: str, data: object) -> dict:
    """Проверить пресет; вернуть нормализованные метаданные."""
    if not NAME_RE.fullmatch(name):
        raise PresetError(f"{name}: имя файла — латиница, цифры, _ и -")
    if not isinstance(data, dict):
        raise PresetError(f"{name}: корень должен быть объектом")
    meta = data.get("_preset", {})
    if not isinstance(meta, dict):
        raise PresetError(f"{name}: _preset должен быть объектом")
    unknown = set(meta) - META_KEYS
    if unknown:
        raise PresetError(f"{name}: неизвестные ключи _preset: {', '.join(sorted(unknown))}")
    out = {"title": meta.get("title") or name, "description": meta.get("description", ""),
           "enabled": meta.get("enabled", False), "priority": meta.get("priority", DEFAULT_PRIORITY),
           "priorities": meta.get("priorities", {})}
    requires = meta.get("requires", {})
    if not isinstance(out["enabled"], bool):
        raise PresetError(f"{name}: _preset.enabled — true/false")
    if type(out["priority"]) is not int:
        raise PresetError(f"{name}: _preset.priority — целое число")
    if not (isinstance(out["priorities"], dict)
            and all(isinstance(k, str) and type(v) is int for k, v in out["priorities"].items())):
        raise PresetError(f"{name}: _preset.priorities — объект {{\"путь\": целое число}}")
    for key in ("title", "description"):
        if not isinstance(out[key], str):
            raise PresetError(f"{name}: _preset.{key} — строка")
    if not isinstance(requires, dict) or set(requires) - {"groups"}:
        raise PresetError(f"{name}: _preset.requires — объект {{\"groups\": […]}}")
    groups = requires.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise PresetError(f"{name}: _preset.requires.groups — список строк")
    out["requires"] = {"groups": list(groups)}
    return out


def load(directory) -> list[dict]:
    """Все пресеты каталога: [{name, meta, data}], по имени файла."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise PresetError(f"{path.name}: символические ссылки не разрешены")
        name = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PresetError(f"{name}: невалидный JSON: {exc}") from exc
        out.append({"name": name, "meta": validate(name, data), "data": data})
    return out


def _by_priority(presets: list[dict]) -> list[dict]:
    return sorted(presets, key=lambda p: (p["meta"]["priority"], p["name"]))


def assemble(base: dict, nodes: dict, presets: list[dict]) -> tuple[dict, list[str]]:
    """Итоговый конфиг: база, ноды и включённые пресеты; (конфиг, предупреждения)."""
    sources = [Source("base.json", base), Source("nodes.json", nodes)]
    for p in presets:
        if p["meta"]["enabled"]:
            data = {k: v for k, v in p["data"].items() if k != "_preset"}
            sources.append(Source(f"presets/{p['name']}", data, p["meta"]["priority"],
                                  p["meta"]["priorities"]))
    return merge(sources)


def client_settings(data: object) -> dict:
    """Проверить clients/settings.json; вернуть нормализованные настройки."""
    if not isinstance(data, dict):
        raise PresetError("clients/settings.json: корень должен быть объектом")
    unknown = set(data) - CLIENT_SETTINGS_KEYS
    if unknown:
        raise PresetError(f"clients/settings.json: неизвестные ключи: {', '.join(sorted(unknown))}")
    url = data.get("rules_url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise PresetError("clients/settings.json: rules_url — адрес раздачи наборов правил (https://…/)")
    interval = data.get("update_interval", "24h")
    if not isinstance(interval, str) or not interval:
        raise PresetError("clients/settings.json: update_interval — строка вида 24h")
    return {"rules_url": url.rstrip("/") + "/", "update_interval": interval}


def for_client(name: str, data: dict, settings: dict | None) -> dict:
    """Пресет для клиента: локальные наборы правил роутера → remote с адресом раздачи."""
    rule_sets = data.get("route", {}).get("rule_set") or []
    if not any(isinstance(rs, dict) and rs.get("type") == "local" for rs in rule_sets):
        return data
    if settings is None:
        raise PresetError(f"{name}: локальные наборы правил клиенту не раздать без rules_url "
                          f"в clients/settings.json")
    remote = []
    for rs in rule_sets:
        if isinstance(rs, dict) and rs.get("type") == "local":
            directory, _, file = str(rs.get("path", "")).rpartition("/")
            if directory + "/" != RULES_DIR or not file:
                raise PresetError(f"{name}: набор {rs.get('tag')} лежит вне {RULES_DIR}, клиенту его не раздать")
            rs = {"type": "remote", "tag": rs.get("tag"),
                  "format": rs.get("format") or ("source" if file.endswith(".json") else "binary"),
                  "url": settings["rules_url"] + file, "update_interval": settings["update_interval"]}
        remote.append(rs)
    return {**data, "route": {**data["route"], "rule_set": remote}}


def assemble_client(base: dict, nodes: dict, presets: list[dict],
                    settings: dict | None) -> tuple[dict, list[str]]:
    """Конфиг клиента: как assemble, но наборы правил пресетов — remote."""
    presets = [{**p, "data": for_client(p["name"], p["data"], settings)} if p["meta"]["enabled"] else p
               for p in presets]
    return assemble(base, nodes, presets)


def node_groups(nodes_path) -> set[str]:
    """Группы нод, которые есть в nodes.json: {name} для каждого {name}-auto-out."""
    try:
        data = json.loads(Path(nodes_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {o["tag"][:-len("-auto-out")] for o in data.get("outbounds", [])
            if isinstance(o, dict) and str(o.get("tag", "")).endswith("-auto-out")}


def status(presets: list[dict], groups: set[str] | None) -> list[dict]:
    """Состояние для админки: метаданные + недостающие группы (None — неизвестно)."""
    out = []
    for p in _by_priority(presets):
        missing = (None if groups is None else
                   [g for g in p["meta"]["requires"]["groups"] if g not in groups])
        out.append({"name": p["name"], **p["meta"], "missing_groups": missing})
    return out


def _read_json(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("корень должен быть объектом")
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m nodes_admin.presets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble", help="склеить базу, ноды и включённые пресеты")
    a.add_argument("--base", required=True)
    a.add_argument("--nodes", required=True)
    a.add_argument("--dir")
    a.add_argument("--client-settings", help="clients/settings.json: сборка клиента (может не существовать)")
    a.add_argument("--out", required=True)
    s = sub.add_parser("list", help="состояние пресетов (JSON)")
    s.add_argument("--dir", required=True)
    s.add_argument("--nodes")
    args = ap.parse_args(argv)
    try:
        presets = load(args.dir) if args.dir else []
    except PresetError as exc:
        print(f"[presets] {exc}", file=sys.stderr)
        return 1
    if args.cmd == "assemble":
        on = [p["name"] for p in _by_priority(presets) if p["meta"]["enabled"]]
        print(f"[presets] включены: {', '.join(on) or 'нет'}", file=sys.stderr)
        try:
            base, nodes = _read_json(args.base), _read_json(args.nodes)
            if args.client_settings is None:
                config, warnings = assemble(base, nodes, presets)
            else:
                settings = (client_settings(_read_json(args.client_settings))
                            if os.path.exists(args.client_settings) else None)
                config, warnings = assemble_client(base, nodes, presets, settings)
        except (OSError, ValueError) as exc:
            print(f"[presets] {exc}", file=sys.stderr)
            return 1
        for line in warnings:
            print(f"[presets] {line}", file=sys.stderr)
        Path(args.out).write_text(dumps(config) + "\n", encoding="utf-8")
        return 0
    groups = node_groups(args.nodes) if args.nodes and os.path.exists(args.nodes) else None
    print(json.dumps(status(presets, groups), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
