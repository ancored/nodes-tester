#!/usr/bin/env python3
"""Простой JSON-мерж конфигов sing-box.

Замена `sing-box merge` для клиентских конфигов. `sing-box merge` разбирает конфиг
типами своей версии и пишет его заново в каноничной форме (дефолтные поля выкинуты,
значения нормализованы, outbounds переставлены) — результат зависел бы от версии
ядра, на котором идёт сборка. Здесь склейка на чистом JSON: содержимое входов
проходит без изменений, типы знать не нужно.

Правила склейки:
  - объекты (dict) сливаются рекурсивно;
  - массивы (list) конкатенируются (base сначала, затем следующие входы);
  - скаляры: последний вход побеждает.

Usage:
    merge-configs.py <output.json> <input1.json> [<input2.json> ...]
"""
import json
import sys


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: merge-configs.py <output> <input1> [input2 ...]")
    out_path = sys.argv[1]
    result = {}
    for path in sys.argv[2:]:
        with open(path, encoding="utf-8") as f:
            result = merge(result, json.load(f))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    json.loads(payload)  # финальная проверка валидности JSON
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(payload)


if __name__ == "__main__":
    main()
