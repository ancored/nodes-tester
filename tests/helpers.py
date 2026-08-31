"""Общие фикстуры для юнит-тестов (stdlib unittest, без pytest)."""

from __future__ import annotations

import json
import os
import tempfile


def temp_dir() -> str:
    return tempfile.mkdtemp(prefix="ntest-")


def make_config(tmp: str, **overrides) -> str:
    """Собрать рабочий config.json во временной папке (пути — в tmp). Возвращает путь."""
    cfg = json.load(open("config/config.json", encoding="utf-8"))
    cfg["scoring"]["file"] = os.path.join(tmp, "score.csv")
    cfg["switching"]["state_file"] = os.path.join(tmp, "switch.json")
    cfg["storage"]["db_file"] = os.path.join(tmp, "stats.db")
    cfg["storage"]["nodes_file"] = os.path.join(tmp, "nodes.json")
    cfg["switching"]["rotation"]["load_balance"]["enabled"] = False
    for k, v in overrides.items():           # мелкий мердж верхнего уровня секций
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    path = os.path.join(tmp, "config.json")
    json.dump(cfg, open(path, "w", encoding="utf-8"))
    return path


def make_runner(tmp: str, tags=None, **overrides):
    """Runner с временными путями и фейковым Clash (без сети). tags — члены nodes-tester."""
    from nodes_tester.config import load_config
    from nodes_tester.runner import Runner

    cfg = load_config(make_config(tmp, **overrides))
    r = Runner(cfg)
    r._base = cfg.run.for_group(cfg.testing_group.tag)
    r._region_cache = {}
    r._backoff = {}
    r._endpoints = {}
    r._host_ep_last = {}
    r.clash = FakeClash(tags or [])
    return r


class FakeClash:
    """Минимальный Clash без сети: члены группы + no-op select."""

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


def fresh_nodes_json(tmp: str, outbounds) -> str:
    path = os.path.join(tmp, "nodes.json")
    json.dump({"outbounds": outbounds}, open(path, "w", encoding="utf-8"))
    return path
