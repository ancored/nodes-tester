"""Общие фикстуры для юнит-тестов (stdlib unittest, без pytest)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

# Консоль Windows в cp1251 не печатает часть вывода тестера (стрелки, тире): тесты падали бы
# на print. Модуль импортируют почти все тесты, discover загружает их до запуска.
for _stream in (sys.stdout, sys.stderr):
    if (hasattr(_stream, "reconfigure")
            and (getattr(_stream, "encoding", "") or "").lower().replace("-", "") != "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def temp_dir() -> str:
    return tempfile.mkdtemp(prefix="ntest-")


def make_config(tmp: str, **overrides) -> str:
    """Собрать рабочий config.json во временной папке (БД — в tmp). Возвращает путь.
    Каталог данных (nodes.json) задаёт load_test_config / make_runner."""
    with open("config/config.example.json", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["storage"] = {"path": os.path.join(tmp, "stats.db")}
    for k, v in overrides.items():           # мелкий мердж верхнего уровня секций
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    path = os.path.join(tmp, "config.json")
    schema_src = os.path.join("config", "config.schema.json")
    if os.path.exists(schema_src):
        shutil.copyfile(schema_src, os.path.join(tmp, "config.schema.json"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return path


def load_test_config(tmp: str, **overrides):
    """Config из make_config с каталогом данных tmp (nodes.json — tmp/nodes.json)."""
    from nodes_tester.config import load_config
    return load_config(make_config(tmp, **overrides), data_dir=tmp)


def make_runner(tmp: str, tags=None, once=False, **overrides):
    """Runner с временными путями и фейковым API sing-box (без сети). tags — члены nodes-tester."""
    from nodes_tester.runner import Runner

    cfg = load_test_config(tmp, **overrides)
    r = Runner(cfg, once=once)
    r._tests_cache = {}
    r._backoff = {}
    r._endpoints = {}
    r._host_ep_last = {}
    r.api = FakeApi(tags or [])
    r._refresh_whitelist = lambda nodes: None     # проверка белых списков качает списки — не в тестах
    return r


class FakeApi:
    """Минимальный API sing-box без сети: члены группы + no-op select."""

    def __init__(self, tags):
        self._tags = list(tags)
        self.selected = []

    def list_group_members(self, group):
        return list(self._tags)

    def select(self, group, node):
        self.selected.append((group, node))

    def current_selection(self, group):
        return ""

    def ping(self):
        pass

    def all_proxies(self):
        return {}


def fresh_nodes_json(tmp: str, outbounds) -> str:
    path = os.path.join(tmp, "nodes.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"outbounds": outbounds}, fh)
    return path


def write_schedule(config_dir, schedule) -> str:
    """config.json с разделом pipeline из расписания в формате API (jobs/times/timeout).
    Возвращает путь к config.json — его принимает Orchestrator."""
    from pathlib import Path
    path = Path(config_dir) / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["pipeline"] = {"timeout": f"{schedule['timeout']}s", **{
        mode: {"enabled": job["enabled"], "at": job["times"]} for mode, job in schedule["jobs"].items()}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)
