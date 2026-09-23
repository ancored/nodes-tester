"""python -m nodes_config — raw_nodes.json (+ groups_params, + user_nodes) → nodes.json.

    python -m nodes_config --raw data/raw/main.json [--raw data/raw/extra.json] \\
        [--groups config/groups_params.json] [--user-nodes config/user_nodes.json] \\
        -o /etc/sing-box-subscribe/nodes.json [--json] [--check]
    python -m nodes_config migrate --from config/ --to config-v2/ [--force]

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

from . import build, migrate, params


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _load_user_nodes(path):
    if not path:
        return []
    nodes = read_json(path)
    if not isinstance(nodes, list):
        raise ValueError(f"{path}: user_nodes должен быть JSON-массивом")
    return nodes


def _load_existing(path):
    if not os.path.exists(path):
        return None
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        _log(f"[config] текущий {path} не читается ({exc}) — будет перезаписан")
        return None


def run_build(raw_paths, groups_path, user_nodes_path, output, check=False, log=_log):
    """Собрать и (если изменилось и не check) записать. → сводка dict."""
    raws = [rawfmt.load(p) for p in raw_paths]
    prm = params.load_path(groups_path)
    fragment, report = build.build(raws, prm, _load_user_nodes(user_nodes_path), log=log)

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


def _build_main(argv):
    ap = argparse.ArgumentParser(prog="python -m nodes_config",
                                 description="raw_nodes.json → nodes.json (переименование, фильтры, группы)")
    ap.add_argument("--raw", action="append", required=True, help="raw_nodes.json (можно несколько)")
    ap.add_argument("--groups", help="groups_params.json (опционально)")
    ap.add_argument("--user-nodes", help="user_nodes.json (опционально)")
    ap.add_argument("-o", "--output", required=True, help="куда писать nodes.json")
    ap.add_argument("--check", action="store_true", help="собрать и сравнить, ничего не писать")
    ap.add_argument("--json", action="store_true", help="сводка JSON в stdout")
    args = ap.parse_args(argv)
    try:
        result, code = run_build(args.raw, args.groups, args.user_nodes, args.output,
                                 check=args.check), 0
        rep = result["report"]
        _log(f"[config] нод {rep['leaf_nodes']}, групп {rep['groups']}, регионы {rep['regions']}, "
             f"отброшено {rep['dropped'] or 0}; +{len(result['added'])} −{len(result['removed'])}")
    except (OSError, ValueError, LockTimeout) as exc:
        _log(f"[config] ОШИБКА: {exc}")
        result, code = {"ok": False, "error": str(exc)}, 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


def _migrate_main(argv):
    ap = argparse.ArgumentParser(prog="python -m nodes_config migrate",
                                 description="конфиги v1 (providers+groups_params) → v2")
    ap.add_argument("--from", dest="src", required=True, help="каталог v1")
    ap.add_argument("--to", dest="dst", required=True, help="каталог v2")
    ap.add_argument("--force", action="store_true", help="перезаписывать существующие файлы")
    args = ap.parse_args(argv)
    try:
        migrate.migrate_dir(args.src, args.dst, force=args.force, log=_log)
    except (OSError, ValueError) as exc:
        _log(f"[migrate] ОШИБКА: {exc}")
        return 1
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["migrate"]:
        return _migrate_main(argv[1:])
    return _build_main(argv)


if __name__ == "__main__":
    sys.exit(main())
