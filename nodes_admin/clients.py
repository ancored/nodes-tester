"""Клиенты из config.json (раздел clients) для build-clients.sh и админки.

    python3 -m nodes_admin.clients -c config.json list     # «имя токен» включённых клиентов
    python3 -m nodes_admin.clients -c config.json output   # каталог публикации (pipeline.clients.output)

Конфиг клиента собирается из базы singbox/clients/base_<имя>.json (с whnodes.json и клиентскими
пресетами) или берётся как есть из singbox/clients/publish/<имя>.json. Публикация — <output>/<имя>.json
и <output>/by-token/<токен>.json: веб-сервер раздаёт только by-token/, имя файла в ссылке — токен.
"""

from __future__ import annotations

import argparse
import os
import sys

TOKEN_DIR = "by-token"


def source(singbox_dir: str, name: str) -> "str | None":
    """Откуда собирается конфиг клиента: base (база + ноды + пресеты), file (готовый файл) или None."""
    clients = os.path.join(singbox_dir, "clients")
    if os.path.isfile(os.path.join(clients, f"base_{name}.json")):
        return "base"
    if os.path.isfile(os.path.join(clients, "publish", f"{name}.json")):
        return "file"
    return None


def state(cfg, singbox_dir: str) -> list[dict]:
    """Клиенты для админки: имя, включён ли, источник, когда опубликован конфиг по токену (без
    самого токена — его админка берёт из config.json)."""
    out = []
    for client in cfg.clients:
        published = os.path.join(cfg.pipeline.clients.output, TOKEN_DIR, client["token"] + ".json")
        try:
            mtime = int(os.stat(published).st_mtime)
        except OSError:
            mtime = None
        out.append({"name": client["name"], "enabled": client["enabled"],
                    "source": source(singbox_dir, client["name"]), "published": mtime})
    return out


def main(argv=None) -> int:
    from nodes_tester.config import ConfigError, load_config
    ap = argparse.ArgumentParser(prog="python3 -m nodes_admin.clients", description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", required=True, help="config.json")
    ap.add_argument("what", choices=("list", "output"))
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"[clients] {exc}", file=sys.stderr)
        return 1
    if args.what == "output":
        print(cfg.pipeline.clients.output)
    else:
        for client in cfg.clients:
            if client["enabled"]:
                print(client["name"], client["token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
