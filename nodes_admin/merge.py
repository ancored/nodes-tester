"""Склейка конфигов sing-box на чистом JSON — замена `sing-box merge`.

Входы — источники с приоритетами. Источник без приоритета (база, ноды) стоит выше всех
приоритетных в том порядке, в каком передан; приоритетные — по числу (меньше — выше), при
равенстве — по порядку передачи. Приоритет можно переопределить для отдельного пути
(`priorities: {"dns.rules": 150}`).

  - объекты сливаются рекурсивно;
  - массивы склеиваются в порядке приоритета источников для этого пути; одинаковые элементы
    схлопываются до первого: объект с тем же `tag` и тем же содержимым — молча, без тега —
    с предупреждением; тот же `tag` с другим содержимым — ошибка;
  - скаляры: побеждает источник выше; проигравшее другое значение — предупреждение, а при
    равном приоритете двух пресетов — ошибка.

Содержимое входов проходит без изменений: типы sing-box знать не нужно, результат не зависит
от версии ядра.

Командная строка (клиентская ветка, build-clients.sh) — все входы без приоритета, первый главный:

    python3 -m nodes_admin.merge OUTPUT INPUT [INPUT ...]
"""

from __future__ import annotations

import json
import sys
from typing import NamedTuple


class MergeError(ValueError):
    pass


class Source(NamedTuple):
    name: str
    data: dict
    priority: int | None = None
    priorities: dict | None = None


def _rank(src: Source, index: int, path: str) -> tuple:
    if src.priority is None:
        return (0, 0, index)
    return (1, (src.priorities or {}).get(path, src.priority), index)


def merge(sources: list[Source]) -> tuple[dict, list[str]]:
    """Склеить источники; вернуть (конфиг, предупреждения). Конфликт — MergeError."""
    warnings: list[str] = []
    entries = [(i, src, src.data) for i, src in enumerate(sources)]
    return _merge("", entries, warnings), warnings


def _merge(path: str, entries: list, warnings: list[str]):
    entries = sorted(entries, key=lambda e: _rank(e[1], e[0], path))
    values = [value for _, _, value in entries]
    where = path or "корень"
    if all(isinstance(v, dict) for v in values):
        keys = list(dict.fromkeys(k for v in values for k in v))
        return {k: _merge(f"{path}.{k}" if path else k,
                          [(i, src, v[k]) for i, src, v in entries if k in v], warnings)
                for k in keys}
    if all(isinstance(v, list) for v in values):
        return _merge_lists(path, entries, warnings)
    if any(isinstance(v, (dict, list)) for v in values):
        kinds = ", ".join(f"{src.name}: {type(v).__name__}" for _, src, v in entries)
        raise MergeError(f"{where}: разные типы значений ({kinds})")
    (i0, winner, value), *rest = entries
    for i, src, other in rest:
        if other == value:
            continue
        if winner.priority is not None and _rank(winner, i0, path)[1] == _rank(src, i, path)[1]:
            raise MergeError(f"{where}: {winner.name} и {src.name} с одинаковым приоритетом "
                             f"задают разные значения")
        warnings.append(f"{where}: значение из {src.name} не применено, действует {winner.name}")
    return value


def _merge_lists(path: str, entries: list, warnings: list[str]) -> list:
    out: list = []
    origin: dict = {}          # tag → (элемент, источник)
    plain: list = []           # (элемент без тега, источник)
    for _, src, items in entries:
        for item in items:
            tag = item.get("tag") if isinstance(item, dict) else None
            if isinstance(tag, str):
                if tag in origin:
                    first, first_src = origin[tag]
                    if first != item:
                        raise MergeError(f"{path}: тег {tag} в {first_src} и {src.name} "
                                         f"описан по-разному")
                    continue
                origin[tag] = (item, src.name)
            else:
                first_src = next((s for seen, s in plain if seen == item), None)
                if first_src is not None:
                    warnings.append(f"{path}: повтор элемента из {src.name} (уже есть в {first_src}) "
                                    f"пропущен")
                    continue
                plain.append((item, src.name))
            out.append(item)
    return out


def dumps(config: dict) -> str:
    return json.dumps(config, ensure_ascii=False, indent=2)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print("usage: python3 -m nodes_admin.merge OUTPUT INPUT [INPUT ...]", file=sys.stderr)
        return 2
    out_path, inputs = args[0], args[1:]
    try:
        sources = []
        for path in inputs:
            with open(path, encoding="utf-8") as f:
                sources.append(Source(path, json.load(f)))
        config, warnings = merge(sources)
    except (OSError, ValueError) as exc:
        print(f"[merge] {exc}", file=sys.stderr)
        return 1
    for line in warnings:
        print(f"[merge] {line}", file=sys.stderr)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dumps(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
