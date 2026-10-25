"""python -m nodes_config — сборка nodes.json роутера и whnodes.json клиентов.

    python -m nodes_config -c config.json --raw raw/router.json -o nodes.json [--json] [--check]
    python -m nodes_config -c config.json --branch clients --nodes nodes.json \
        --candidates client-candidates.json -o whnodes.json

Роутер — из raw подписок; клиенты — из нод nodes.json, прошедших проверки клиентских групп на
роутере (кандидатов пишет тестер; данные старше --max-age — ошибка: тестер не работает).
Файл пишется ТОЛЬКО при изменении содержимого (JSON-сравнение с текущим файлом) —
нет изменений = нечего применять, sing-box не перезапускается.
Лог — stderr; --json — сводка в stdout: changed/written, added/removed теги, отчёт сборки.
Коды выхода: 0 — успех (изменилось или нет — в сводке); 1 — ошибка (старый файл цел).
"""

import argparse
import json
import os
import sys
import time

from nodes_common import raw as rawfmt
from nodes_common.fileio import LockTimeout, atomic_write_text, dumps_json, file_lock, read_json

from . import build

CANDIDATES_MAX_AGE = 1800.0     # тестер обновляет кандидатов раз в 5 минут


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


def load_candidates(path, max_age=CANDIDATES_MAX_AGE, now=None):
    """{группа: теги} из client-candidates.json тестера; нет файла или он устарел — ValueError."""
    try:
        data = read_json(path)
    except OSError as exc:
        raise ValueError(f"нет кандидатов клиентских групп ({path}): тестер не запущен?") from exc
    age = (now or time.time()) - float(data.get("ts") or 0)
    if age > max_age:
        raise ValueError(f"кандидатам клиентских групп {age / 60:.0f} мин: тестер их не обновляет")
    return data.get("groups") or {}


def write_fragment(fragment, report, output, check=False, log=_log, **extra):
    """Записать фрагмент, если он изменился (и не check). → сводка dict."""
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
                "removed": sorted(old_tags - set(new_tags)), "report": report, **extra}

    if check:
        return finish()
    with file_lock(output):
        return finish()


def run_build(raw_paths, cfg, output, check=False, log=_log):
    """Собрать nodes.json роутера и (если изменилось и не check) записать. → сводка dict."""
    raws = [rawfmt.load(p) for p in raw_paths]
    fragment, report = build.build(raws, cfg, log=log)
    return write_fragment(fragment, report, output, check, log,
                          raw_hashes=[r["nodes_hash"] for r in raws])


def run_clients(nodes_path, candidates_path, cfg, output, check=False, log=_log):
    """Собрать whnodes.json из nodes.json роутера и кандидатов клиентских групп."""
    fragment, report = build.build_clients(read_json(nodes_path), load_candidates(candidates_path), cfg)
    return write_fragment(fragment, report, output, check, log)


def main(argv=None):
    from nodes_tester.config import load_config
    ap = argparse.ArgumentParser(prog="python -m nodes_config",
                                 description="raw + config.json → nodes.json; nodes.json → whnodes.json")
    ap.add_argument("-c", "--config", required=True, help="config.json")
    ap.add_argument("--branch", choices=("router", "clients"), default="router")
    ap.add_argument("--raw", action="append", help="router: raw_nodes.json (можно несколько)")
    ap.add_argument("--nodes", help="clients: nodes.json роутера")
    ap.add_argument("--candidates", help="clients: client-candidates.json тестера")
    ap.add_argument("-o", "--output", required=True, help="куда писать nodes.json / whnodes.json")
    ap.add_argument("--check", action="store_true", help="собрать и сравнить, ничего не писать")
    ap.add_argument("--json", action="store_true", help="сводка JSON в stdout")
    args = ap.parse_args(argv)
    if args.branch == "router" and not args.raw:
        ap.error("для router нужен --raw")
    if args.branch == "clients" and not (args.nodes and args.candidates):
        ap.error("для clients нужны --nodes и --candidates")
    try:
        cfg = load_config(args.config)
        if args.branch == "router":
            result = run_build(args.raw, cfg, args.output, check=args.check)
            rep = result["report"]
            built = f"нод {rep['leaf_nodes']}, групп {rep['groups']}, отброшено {rep['dropped'] or 0}"
        else:
            result = run_clients(args.nodes, args.candidates, cfg, args.output, check=args.check)
            rep = result["report"]
            groups = ", ".join(f"{tag} {n}" for tag, n in rep["groups"].items())
            built = f"клиенты: нод {rep['leaf_nodes']} ({groups}), отброшено фильтрами {rep['dropped'] or 0}"
        _log(f"[config] {built}; +{len(result['added'])} −{len(result['removed'])}")
        code = 0
    except (OSError, ValueError, LockTimeout) as exc:
        _log(f"[config] ОШИБКА: {exc}")
        result, code = {"ok": False, "error": str(exc)}, 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
