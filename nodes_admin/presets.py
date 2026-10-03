"""Пресеты правил sing-box: отдельные JSON-файлы, склеиваемые с базой и нодами.

Пресет — фрагмент конфига sing-box плюс метаданные в ключе `_preset`:

    {
      "_preset": {"title": "US", "description": "…", "enabled": true,
                  "priority": 450, "dns_priority": 450, "requires": {"groups": ["us"]}},
      "dns":   {"servers": […], "rules": […]},
      "route": {"rules": […], "rule_set": […]},
      "outbounds": […]
    }

Итоговый конфиг — `sing-box merge` nodes.json, базы и включённых пресетов: массивы
склеиваются в порядке входов, а порядок входов sing-box берёт по ИМЕНАМ ФАЙЛОВ, не по
аргументам. Поэтому все входы кладутся в один каталог: 10-nodes.json, 20-base.json,
30-preset-NNN.json. Каждый пресет подаётся двумя фрагментами без `_preset`:
всё, кроме dns.rules, — по `priority`; dns.rules — по `dns_priority` (по умолчанию =
priority). При равном приоритете порядок — по имени файла. Внутри пресета порядок правил
не меняется. Так порядок правил маршрута и DNS задаётся независимо.

`requires.groups` — группы нод ({name}-auto-out), без которых пресет ссылается на
несуществующий outbound; окончательную проверку делает `sing-box check`.

Командная строка (для apply-nodes.sh):

    python3 -m nodes_admin.presets fragments --dir DIR --out-dir TMP   # пути фрагментов
    python3 -m nodes_admin.presets list --dir DIR [--nodes nodes.json]  # состояние (JSON)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOP_KEYS = {"_preset", "dns", "route", "outbounds", "endpoints"}
_DNS_KEYS = {"servers", "rules"}
_ROUTE_KEYS = {"rules", "rule_set"}
DEFAULT_PRIORITY = 500


class PresetError(ValueError):
    pass


def validate(name: str, data: object) -> dict:
    """Проверить пресет; вернуть нормализованные метаданные."""
    if not NAME_RE.fullmatch(name):
        raise PresetError(f"{name}: имя файла — латиница, цифры, _ и -")
    if not isinstance(data, dict):
        raise PresetError(f"{name}: корень должен быть объектом")
    extra = set(data) - _TOP_KEYS
    if extra:
        raise PresetError(f"{name}: недопустимые разделы {', '.join(sorted(extra))} "
                          f"(разрешены dns, route, outbounds, endpoints)")
    for key, allowed in (("dns", _DNS_KEYS), ("route", _ROUTE_KEYS)):
        part = data.get(key, {})
        if not isinstance(part, dict):
            raise PresetError(f"{name}: {key} должен быть объектом")
        bad = set(part) - allowed
        if bad:
            raise PresetError(f"{name}: {key}.{', '.join(sorted(bad))} — в пресете разрешены "
                              f"только {', '.join(sorted(allowed))}")
        for sub, value in part.items():
            if not isinstance(value, list):
                raise PresetError(f"{name}: {key}.{sub} должен быть массивом")
    for key in ("outbounds", "endpoints"):
        if key in data and not isinstance(data[key], list):
            raise PresetError(f"{name}: {key} должен быть массивом")
    meta = data.get("_preset", {})
    if not isinstance(meta, dict):
        raise PresetError(f"{name}: _preset должен быть объектом")
    out = {"title": meta.get("title") or name, "description": meta.get("description", ""),
           "enabled": meta.get("enabled", False), "priority": meta.get("priority", DEFAULT_PRIORITY)}
    out["dns_priority"] = meta.get("dns_priority", out["priority"])
    requires = meta.get("requires", {})
    if not isinstance(out["enabled"], bool):
        raise PresetError(f"{name}: _preset.enabled — true/false")
    for key in ("priority", "dns_priority"):
        if type(out[key]) is not int:
            raise PresetError(f"{name}: _preset.{key} — целое число")
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


def fragments(presets: list[dict]) -> list[dict]:
    """Фрагменты для sing-box merge в нужном порядке (только включённые пресеты)."""
    enabled = [p for p in presets if p["meta"]["enabled"]]
    main, dns_rules = [], []
    for p in sorted(enabled, key=lambda p: (p["meta"]["priority"], p["name"])):
        frag = {k: v for k, v in p["data"].items() if k != "_preset"}
        dns = dict(frag.get("dns", {}))
        dns.pop("rules", None)
        if dns:
            frag["dns"] = dns
        else:
            frag.pop("dns", None)
        if frag:
            main.append(frag)
    for p in sorted(enabled, key=lambda p: (p["meta"]["dns_priority"], p["name"])):
        rules = p["data"].get("dns", {}).get("rules")
        if rules:
            dns_rules.append({"dns": {"rules": rules}})
    return main + dns_rules


def write_fragments(presets: list[dict], out_dir) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, frag in enumerate(fragments(presets)):
        path = out_dir / f"30-preset-{i:03d}.json"
        path.write_text(json.dumps(frag, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(str(path))
    return paths


# sing-box merge склеивает массивы как есть, одинаковые элементы не схлопываются. Дубль тега
# outbound sing-box отвергает сам, а дубль DNS-сервера или rule_set проходит check молча.
def duplicate_tags(config: dict) -> list[str]:
    """Повторяющиеся теги склеенного конфига: ["dns.servers: bootstrap", …]."""
    sections = {
        "inbounds": config.get("inbounds", []),
        "outbounds+endpoints": config.get("outbounds", []) + config.get("endpoints", []),
        "dns.servers": config.get("dns", {}).get("servers", []),
        "route.rule_set": config.get("route", {}).get("rule_set", []),
        "services": config.get("services", []),
    }
    out = []
    for section, items in sections.items():
        seen = set()
        for item in items:
            tag = item.get("tag") if isinstance(item, dict) else None
            if tag in seen and f"{section}: {tag}" not in out:
                out.append(f"{section}: {tag}")
            elif tag:
                seen.add(tag)
    return out


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
    for p in sorted(presets, key=lambda p: (p["meta"]["priority"], p["name"])):
        missing = (None if groups is None else
                   [g for g in p["meta"]["requires"]["groups"] if g not in groups])
        out.append({"name": p["name"], **p["meta"], "missing_groups": missing})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m nodes_admin.presets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fragments", help="записать фрагменты включённых пресетов, вывести пути")
    f.add_argument("--dir", required=True)
    f.add_argument("--out-dir", required=True)
    f.add_argument("--log", action="store_true", help="список включённых — в stderr")
    s = sub.add_parser("list", help="состояние пресетов (JSON)")
    s.add_argument("--dir", required=True)
    s.add_argument("--nodes")
    t = sub.add_parser("check-tags", help="проверить склеенный конфиг на повторяющиеся теги")
    t.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "check-tags":
        try:
            dups = duplicate_tags(json.loads(Path(args.config).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"[presets] {args.config}: {exc}", file=sys.stderr)
            return 1
        if dups:
            print(f"[presets] повторяющиеся теги: {', '.join(dups)}", file=sys.stderr)
            return 1
        return 0
    try:
        presets = load(args.dir)
    except PresetError as exc:
        print(f"[presets] {exc}", file=sys.stderr)
        return 1
    if args.cmd == "fragments":
        if args.log:
            on = [p["name"] for p in sorted(presets, key=lambda p: (p["meta"]["priority"], p["name"]))
                  if p["meta"]["enabled"]]
            print(f"[presets] включены: {', '.join(on) or 'нет'}", file=sys.stderr)
        for path in write_fragments(presets, args.out_dir):
            print(path)
        return 0
    groups = node_groups(args.nodes) if args.nodes and os.path.exists(args.nodes) else None
    print(json.dumps(status(presets, groups), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
