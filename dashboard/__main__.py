"""python -m dashboard [--config config/config.json] [--host ..] [--port ..] [--interval ..]"""

from __future__ import annotations

import argparse
import sys

from nodes_tester.config import load_config
from .server import serve


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(prog="dashboard",
                                     description="Веб-дашборд результатов nodes-tester (read-only)")
    parser.add_argument("-c", "--config", default="config/config.json")
    parser.add_argument("--host", default="0.0.0.0", help="адрес прослушивания (по умолчанию все интерфейсы)")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--interval", type=int, default=10, help="период авто-обновления страницы, сек")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    serve(cfg, args.host, args.port, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
