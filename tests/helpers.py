"""Общие фикстуры для юнит-тестов (stdlib unittest, без pytest)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile


def temp_dir() -> str:
    return tempfile.mkdtemp(prefix="ntest-")


def make_config(tmp: str, **overrides) -> str:
    """Собрать рабочий config.json во временной папке (пути — в tmp). Возвращает путь."""
    with open("config/config.example.json", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["storage"]["db_file"] = os.path.join(tmp, "stats.db")
    cfg["storage"]["nodes_file"] = os.path.join(tmp, "nodes.json")
    cfg["dashboard"]["providers_file"] = os.path.join(tmp, "providers.json")
    cfg["switching"]["rotation"]["load_balance"]["enabled"] = False
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


def make_runner(tmp: str, tags=None, **overrides):
    """Runner с временными путями и фейковым API sing-box (без сети). tags — члены nodes-tester."""
    from nodes_tester.config import load_config
    from nodes_tester.runner import Runner

    cfg = load_config(make_config(tmp, **overrides))
    r = Runner(cfg)
    r._base = cfg.run.for_group(cfg.testing_group.tag)
    r._region_cache = {}
    r._tests_cache = {}
    r._backoff = {}
    r._endpoints = {}
    r._host_ep_last = {}
    r.api = FakeApi(tags or [])
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
