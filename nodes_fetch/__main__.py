"""python -m nodes_fetch — подписки ветви из config.json → raw_nodes.json.

    python -m nodes_fetch -c config.json --branch router -o raw/router.json
    python -m nodes_fetch -c config.json --dry-run --only LUNA --json   # проверить подписку

Лог — в stderr; --json печатает в stdout одну строку-сводку (для оркестратора).
Коды выхода: 0 — записано (или dry-run); 2 — guard отказал в записи (старый файл цел);
1 — ошибка (некорректный конфиг, занят файл, …).
"""

import argparse
import json
import os
import sys

from nodes_common import raw as rawfmt
from nodes_common.fileio import LockTimeout, atomic_write_text, dumps_json, file_lock

from . import device as devicemod
from . import fetch, happ_keys


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _load_previous(path):
    if not path or not os.path.exists(path):
        return None
    try:
        return rawfmt.load(path)
    except (OSError, ValueError) as exc:
        _log(f"[fetch] прошлый raw не прочитан ({exc}) — работаем без last-good")
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m nodes_fetch",
                                 description="Подписки → raw_nodes.json")
    ap.add_argument("-c", "--config", required=True, help="config.json")
    ap.add_argument("--branch", choices=("router", "clients"), default="router",
                    help="ветвь сборки: подписки с этой ветвью (по умолчанию router)")
    ap.add_argument("-o", "--output", help="куда писать raw_nodes.json")
    ap.add_argument("--only", action="append", metavar="TAG",
                    help="обновить только эти подписки (остальные — из прошлого raw)")
    ap.add_argument("--dry-run", action="store_true", help="ничего не записывать")
    ap.add_argument("--force", action="store_true", help="записать, даже если сработал guard")
    ap.add_argument("--json", action="store_true", help="сводка JSON в stdout")
    ap.add_argument("--device", metavar="PATH",
                    help="device.json с HWID (по умолчанию — рядом с config.json)")
    ap.add_argument("--happ-keys", metavar="PATH",
                    help="кэш ключей Happ (по умолчанию happ-keys.json рядом с device.json)")
    args = ap.parse_args(argv)
    if not args.output and not args.dry_run:
        ap.error("нужен -o/--output (или --dry-run)")

    def report(result, code):
        if args.json:
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return code

    from nodes_tester.config import load_config
    try:
        cfg = load_config(args.config)
        providers = fetch.load_providers(fetch.providers_from_config(cfg, args.branch), log=_log)
    except (OSError, ValueError) as exc:
        _log(f"[fetch] ОШИБКА конфига: {exc}")
        return report({"ok": False, "error": str(exc)}, 1)

    base_dir = os.path.dirname(os.path.abspath(args.config))
    name = "providers"

    def do_run():
        previous = _load_previous(args.output)
        device = devicemod.load(args.device or devicemod.default_path(args.config), log=_log)
        raw = fetch.run(providers, base_dir, name=name, previous=previous,
                        only=args.only, log=_log, device=device,
                        happ_keys=args.happ_keys or happ_keys.default_path(args.config))
        if args.dry_run:
            return fetch.summary(raw, previous), 0
        try:
            if not args.force:
                fetch.check_guard(raw, previous, providers["fetch"]["min_ratio"])
        except fetch.GuardError as exc:
            _log(f"[fetch] GUARD: {exc}")
            return dict(fetch.summary(raw, previous), error=str(exc)), 2
        atomic_write_text(args.output, dumps_json(raw))
        _log(f"[fetch] записано: {args.output} (нод {len(raw['nodes'])})")
        return fetch.summary(raw, previous, written=True, output=args.output), 0

    try:
        if args.dry_run:
            result, code = do_run()
        else:
            with file_lock(args.output):
                result, code = do_run()
    except LockTimeout as exc:
        _log(f"[fetch] ОШИБКА: {exc}")
        return report({"ok": False, "error": str(exc)}, 1)
    return report(result, code)


if __name__ == "__main__":
    sys.exit(main())
