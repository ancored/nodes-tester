"""Golden-харнесс генератора nodes.json: запись сетевых ответов подписок и офлайн-повтор.

Инвариант рефактора (REFACTOR-MODULES.md, D8): на одних и тех же входах новый конвейер
(nodes_fetch → nodes_config) обязан выдать тот же nodes.json (JSON-равенство), что
`python -m subscribe` до рефактора. Для этого сетевые ответы записываются один раз, а дальше генерация
гоняется офлайн.

Набор (set) — каталог:
    config/          копия набора конфигов (providers/groups_params/user_nodes/awg…)
    responses.json   записанные ответы: {"http": {url: {status, b64}|null}, "happ": {url: text|null}}
    golden.json      эталонный nodes.json (выход генератора на этих ответах)

Команды (запуск из любого места, stdlib + зависимости subscribe):
    python harness.py record --project DIR --config-dir DIR --out SET   # живая сеть → ответы
    python harness.py replay --project DIR --set SET [--out FILE]      # офлайн → nodes.json
    python harness.py golden --project DIR --set SET                   # replay → SET/golden.json

`record` безопасно гонять на роутере: пишет только в --out, боевой nodes.json не трогает.
"""

import argparse
import base64
import contextlib
import json
import os
import runpy
import shutil
import sys
import tempfile
import time


class _FakeResponse:
    """Минимум requests.Response, который использует subscribe.main."""

    def __init__(self, status, content):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


def _net_points(project):
    """Точки перехвата сети для кода в project → (subscribe_dir, http_obj, http_name, happ_mod).
    Новый код (Ф1+): nodes_fetch.util.http_get + nodes_fetch.happ._fetch.
    Старый (роутер до рефактора): subscribe/tool.getResponse + subscribe/happ._fetch."""
    sub = os.path.join(project, "subscribe")
    for p in (project, sub):
        if p not in sys.path:
            sys.path.insert(0, p)
    if os.path.isdir(os.path.join(project, "nodes_fetch")):
        from nodes_fetch import happ, util  # noqa: E402
        return sub, util, "http_get", happ
    import happ  # noqa: E402  (плоские импорты старого subscribe)
    import tool  # noqa: E402
    return sub, tool, "getResponse", happ


def _no_network(*args, **kwargs):
    raise AssertionError("replay: попытка реального сетевого запроса")


@contextlib.contextmanager
def _patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _prepare_config(config_dir, out_path):
    """Копия набора конфигов во временный каталог с save_config_path → out_path."""
    tmp = tempfile.mkdtemp(prefix="golden-cfg-")
    dst = os.path.join(tmp, "config")
    shutil.copytree(config_dir, dst)
    prov_path = os.path.join(dst, "providers.json")
    with open(prov_path, encoding="utf-8") as f:
        prov = json.load(f)
    prov["save_config_path"] = out_path
    prov.pop("auto_backup", None)
    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    return tmp, dst


def _run_main(sub_dir, cfg_dir):
    """Запустить генератор subscribe на наборе cfg_dir. Новая обёртка (Ф2+) — функция
    main(argv); старый код (роутер) — скрипт с разбором sys.argv в `if __name__`."""
    path = os.path.join(sub_dir, "main.py")
    module = runpy.run_path(path, run_name="subscribe_main")
    if callable(module.get("main")):
        code = module["main"](["--config-dir", cfg_dir])
        if code:
            raise RuntimeError(f"subscribe.main вернул {code}")
        return
    argv = sys.argv
    sys.argv = ["main.py", "--config-dir", cfg_dir]
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        sys.argv = argv


def record(project, config_dir, out_set):
    sub, http_obj, http_name, happ = _net_points(project)
    rec = {"http": {}, "happ": {}}
    real_get, real_fetch = getattr(http_obj, http_name), happ._fetch

    def get(url, *args, **kwargs):
        r = real_get(url, *args, **kwargs)
        rec["http"][url] = None if r is None else {
            "status": r.status_code, "b64": base64.b64encode(r.content).decode()}
        return r

    def fetch(url, headers, *args, **kwargs):
        try:
            text = real_fetch(url, headers, *args, **kwargs)
        except Exception:
            rec["happ"][url] = None
            raise
        rec["happ"][url] = text
        return text

    os.makedirs(out_set, exist_ok=True)
    tmp, cfg = _prepare_config(config_dir, os.path.join(out_set, "recorded_nodes.json"))
    try:
        with _patched(http_obj, http_name, get), _patched(happ, "_fetch", fetch):
            _run_main(sub, cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if os.path.exists(os.path.join(out_set, "config")):
        shutil.rmtree(os.path.join(out_set, "config"))
    shutil.copytree(config_dir, os.path.join(out_set, "config"),
                    ignore=shutil.ignore_patterns("config.json", "config.schema.json",
                                                  "config.example.json", "__pycache__"))
    with open(os.path.join(out_set, "responses.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"[golden] записано: http={len(rec['http'])} happ={len(rec['happ'])} → {out_set}")


@contextlib.contextmanager
def _offline(project, set_dir):
    """Сеть → записанные ответы набора; любые реальные запросы запрещены.
    → каталог subscribe (для _run_main)."""
    sub, http_obj, http_name, happ = _net_points(project)
    import requests
    import urllib.request
    with open(os.path.join(set_dir, "responses.json"), encoding="utf-8") as f:
        rec = json.load(f)

    def get(url, *args, **kwargs):
        if url not in rec["http"]:
            raise KeyError(f"незаписанный HTTP-запрос: {url}")
        r = rec["http"][url]
        return None if r is None else _FakeResponse(r["status"], base64.b64decode(r["b64"]))

    def fetch(url, headers, *args, **kwargs):
        if url not in rec["happ"]:
            raise KeyError(f"незаписанный happ-запрос: {url}")
        if rec["happ"][url] is None:
            raise OSError("записанный сбой happ-подписки")
        return rec["happ"][url]

    with contextlib.ExitStack() as stack:
        for obj, name, value in ((http_obj, http_name, get), (happ, "_fetch", fetch),
                                 (time, "sleep", lambda s: None),
                                 (requests, "get", _no_network),
                                 (requests, "request", _no_network),
                                 (urllib.request, "urlopen", _no_network)):
            stack.enter_context(_patched(obj, name, value))
        yield sub


def replay(project, set_dir, out_path):
    """Офлайн-генерация прежним интерфейсом `python -m subscribe` (конфиги v1).
    Незаписанный запрос — ошибка (KeyError): генератор стал ходить в сеть иначе."""
    tmp, cfg = _prepare_config(os.path.join(set_dir, "config"), out_path)
    try:
        with _offline(project, set_dir) as sub:
            _run_main(sub, cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def replay_pipeline(project, set_dir, out_path):
    """Офлайн-генерация НОВЫМ конвейером (Ф2+): migrate v1→v2 → CLI nodes_fetch → CLI
    nodes_config — ровно так, как их будут звать оркестратор и роутерные скрипты."""
    from nodes_config import __main__ as config_cli
    from nodes_config import migrate
    from nodes_fetch import __main__ as fetch_cli
    tmp = tempfile.mkdtemp(prefix="golden-v2-")
    try:
        v2 = os.path.join(tmp, "v2")
        migrate.migrate_dir(os.path.join(set_dir, "config"), v2, log=lambda m: None)
        raw = os.path.join(tmp, "raw.json")
        with _offline(project, set_dir):
            code = fetch_cli.main(["-p", os.path.join(v2, "providers.json"), "-o", raw])
        if code:
            raise RuntimeError(f"nodes_fetch вернул {code}")
        args = ["--raw", raw, "--groups", os.path.join(v2, "groups_params.json"), "-o", out_path]
        if os.path.exists(os.path.join(v2, "user_nodes.json")):
            args += ["--user-nodes", os.path.join(v2, "user_nodes.json")]
        code = config_cli.main(args)
        if code:
            raise RuntimeError(f"nodes_config вернул {code}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("record")
    r.add_argument("--project", required=True)
    r.add_argument("--config-dir", required=True)
    r.add_argument("--out", required=True)
    p = sp.add_parser("replay")
    p.add_argument("--project", required=True)
    p.add_argument("--set", required=True)
    p.add_argument("--out", required=True)
    g = sp.add_parser("golden")
    g.add_argument("--project", required=True)
    g.add_argument("--set", required=True)
    a = ap.parse_args()
    project = os.path.abspath(a.project)
    if a.cmd == "record":
        record(project, os.path.abspath(a.config_dir), os.path.abspath(a.out))
    elif a.cmd == "replay":
        replay(project, os.path.abspath(a.set), os.path.abspath(a.out))
    else:
        replay(project, os.path.abspath(a.set), os.path.join(os.path.abspath(a.set), "golden.json"))


if __name__ == "__main__":
    main()
