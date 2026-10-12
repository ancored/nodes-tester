"""python -m nodes_config — raw ветви + config.json → nodes.json (whnodes.json у клиентов).

    python -m nodes_config -c config.json --branch router --raw raw/router.json -o nodes.json [--json] [--check]

nodes.json пишется ТОЛЬКО при изменении содержимого (JSON-сравнение с текущим файлом) —
нет изменений = нечего применять, sing-box не перезапускается.
Лог — stderr; --json — сводка в stdout: changed/written, added/removed теги, отчёт сборки.
Коды выхода: 0 — успех (изменилось или нет — в сводке); 1 — ошибка (старый файл цел).
"""

import argparse
import json
import os
import sys

from nodes_common import raw as rawfmt
from nodes_common.fileio import LockTimeout, atomic_write_text, dumps_json, file_lock, read_json

from . import build


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _load_existing(path):
    if not os.path.exists(path):
        return None
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        _log(f"[config] текущий {path} не читается ({exc}) — будет перезаписан")
        return None


def run_build(raw_paths, cfg, branch, output, check=False, log=_log):
    """Собрать и (если изменилось и не check) записать. → сводка dict."""
    raws = [rawfmt.load(p) for p in raw_paths]
    fragment, report = build.build(raws, cfg, branch, log=log)

    def finish():
        existing = _load_existing(output)
        changed = existing != fragment
        old_tags = set(build.tags(existing)) if existing else set()
        new_tags = build.tags(fragment)
        written = False
        if changed and not check:
            atomic_write_text(output, dumps_json(fragment))
            written = True
            log(f"[config] записано: {output}")
        elif not changed:
            log(f"[config] без изменений: {output}")
        return {"ok": True, "changed": changed, "written": written, "output": output,
                "added": [t for t in new_tags if t not in old_tags],
                "removed": sorted(old_tags - set(new_tags)),
                "raw_hashes": [r["nodes_hash"] for r in raws], "report": report}

    if check:
        return finish()
    with file_lock(output):
        return finish()


def main(argv=None):
    from nodes_tester.config import load_config
    ap = argparse.ArgumentParser(prog="python -m nodes_config",
                                 description="raw ветви + config.json → nodes.json (имена, фильтры, группы)")
    ap.add_argument("-c", "--config", required=True, help="config.json")
    ap.add_argument("--branch", choices=("router", "clients"), default="router")
    ap.add_argument("--raw", action="append", required=True, help="raw_nodes.json (можно несколько)")
    ap.add_argument("-o", "--output", required=True, help="куда писать nodes.json")
    ap.add_argument("--check", action="store_true", help="собрать и сравнить, ничего не писать")
    ap.add_argument("--json", action="store_true", help="сводка JSON в stdout")
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        cfg = load_config(args.config)
        result, code = run_build(args.raw, cfg, args.branch, args.output, check=args.check), 0
        rep = result["report"]
        _log(f"[config] нод {rep['leaf_nodes']}, групп {rep['groups']}, "
             f"отброшено {rep['dropped'] or 0}; +{len(result['added'])} −{len(result['removed'])}")
    except (OSError, ValueError, LockTimeout) as exc:
        _log(f"[config] ОШИБКА: {exc}")
        result, code = {"ok": False, "error": str(exc)}, 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
