"""Golden-харнесс генератора nodes.json: запись сетевых ответов подписок и офлайн-повтор.

Инвариант: на одних и тех же входах конвейер (nodes_fetch → nodes_config)
выдаёт эталонный nodes.json (JSON-равенство). Сетевые ответы записываются один раз, дальше
генерация гоняется офлайн.

Набор (set) — каталог:
    config/          копия набора конфигов (providers/groups_params/user_nodes/awg…)
    responses.json   записанные ответы: {"http": {url: {status, b64}|null}, "happ": {url: text|null}}
    golden.json      эталонный nodes.json (выход генератора на этих ответах)

Команды (запуск из любого места, stdlib + зависимости nodes_fetch):
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
import shutil
import sys
import tempfile
import time


class _FakeResponse:
    """Минимум requests.Response, который использует nodes_fetch."""

    def __init__(self, status, content):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


def _net_points(project):
    """Точки перехвата сети: nodes_fetch.util.http_get и nodes_fetch.happ._fetch."""
    if project not in sys.path:
        sys.path.insert(0, project)
    from nodes_fetch import happ, util  # noqa: E402
    return util, "http_get", happ


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


def record(project, config_dir, out_set):
    """Живая сеть: прогнать nodes_fetch по набору и записать ответы подписок."""
    from nodes_fetch import __main__ as fetch_cli
    http_obj, http_name, happ = _net_points(project)
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
    tmp = tempfile.mkdtemp(prefix="golden-rec-")
    try:
        with _patched(http_obj, http_name, get), _patched(happ, "_fetch", fetch):
            fetch_cli.main(["-p", os.path.join(config_dir, "providers.json"),
                            "--device", os.path.join(tmp, "device.json"),
                            "-o", os.path.join(tmp, "raw.json")])
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
    """Сеть → записанные ответы набора; любые реальные запросы запрещены."""
    http_obj, http_name, happ = _net_points(project)
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
        yield


def replay(project, set_dir, out_path):
    """Офлайн-генерация конвейером: CLI nodes_fetch → CLI nodes_config —
    ровно так, как их зовут конвейер и роутерные скрипты. Незаписанный запрос — ошибка."""
    from nodes_config import __main__ as config_cli
    from nodes_fetch import __main__ as fetch_cli
    tmp = tempfile.mkdtemp(prefix="golden-")
    try:
        cfg = os.path.join(set_dir, "config")
        raw = os.path.join(tmp, "raw.json")
        with _offline(project, set_dir):
            code = fetch_cli.main(["-p", os.path.join(cfg, "providers.json"), "-o", raw,
                                    "--device", os.path.join(tmp, "device.json")])
        if code:
            raise RuntimeError(f"nodes_fetch вернул {code}")
        args = ["--raw", raw, "--groups", os.path.join(cfg, "groups_params.json"), "-o", out_path]
        if os.path.exists(os.path.join(cfg, "user_nodes.json")):
            args += ["--user-nodes", os.path.join(cfg, "user_nodes.json")]
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
