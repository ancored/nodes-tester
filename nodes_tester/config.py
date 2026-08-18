"""Загрузка и валидация конфигурации проекта (config.json)."""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

# --- Подключение к sing-box (socks) --------------------------------------


@dataclass
class ConnectionConfig:
    type: str = "socks5"
    host: str = "127.0.0.1"
    port: int = 2080
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def proxy_url(self) -> str:
        """socks5h:// — DNS резолвится на стороне ноды (exit)."""
        auth = ""
        if self.username:
            auth = self.username
            if self.password:
                auth += f":{self.password}"
            auth += "@"
        return f"socks5h://{auth}{self.host}:{self.port}"


@dataclass
class ClashApiConfig:
    base_url: str = "http://127.0.0.1:9090"
    secret: str = ""
    timeout: float = 5.0


@dataclass
class SelectorConfig:
    group: str = "nodes-tester"
    exclude: list[str] = field(default_factory=lambda: ["DIRECT", "REJECT"])


@dataclass
class TestingGroupConfig:
    """Один тест-юнит: socks-подключение + selector-группа для перебора."""
    tag: str = "main"
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    selector: SelectorConfig = field(default_factory=SelectorConfig)


@dataclass
class RegionGroupsConfig:
    """Как определять регион ноды.

    recognition:
      by_selector — регион из тега под-селектора (двухуровневая nodes-tester);
      parse       — коарс-регион из страны в имени ноды (eu/us/ru/other);
      manually    — явные списки регион→теги нод (см. list).
    """
    enabled: bool = True
    recognition: str = "by_selector"
    list: list[dict] = field(default_factory=list)      # [{tag, nodes_list}]
    exclude: list[str] = field(default_factory=list)     # регионы, которые не тестировать

    def manual_map(self) -> dict[str, str]:
        """node_tag -> region_tag (для recognition=manually)."""
        out = {}
        for rg in self.list:
            for node in rg.get("nodes_list", []):
                out[node] = rg.get("tag", "")
        return out


# --- Run: default + послойные override'ы --------------------------------

_DEFAULT_TESTS = ["connectivity", "latency", "jitter", "download", "stability", "reachability"]


@dataclass
class RunParams:
    tests_enabled: list[str] = field(default_factory=lambda: list(_DEFAULT_TESTS))
    loop: bool = True
    pass_pause: float = 60.0
    switch_delay: float = 1.0
    group_pause: float = 30.0
    rounds: int = 1
    request_timeout: float = 10.0
    restore_selection: bool = True

    def merged(self, overrides: dict) -> "RunParams":
        """Копия с наложенными override'ами (мелкий мердж по известным полям)."""
        fields = {f.name for f in dataclasses.fields(self)}
        patch = {k: v for k, v in (overrides or {}).items() if k in fields}
        return dataclasses.replace(self, **patch)


@dataclass
class RunConfig:
    default: RunParams = field(default_factory=RunParams)
    group_overrides: dict[str, dict] = field(default_factory=dict)   # testing_group_tag -> overrides
    region_overrides: dict[str, dict] = field(default_factory=dict)  # region_group_tag -> overrides

    def for_group(self, group_tag: str) -> RunParams:
        return self.default.merged(self.group_overrides.get(group_tag, {}))

    def for_region(self, base: RunParams, region_tag: str) -> RunParams:
        return base.merged(self.region_overrides.get(region_tag, {}))


@dataclass
class ReportConfig:
    enabled: bool = True
    dir: str = "./results"
    format: str = "jsonl"
    filename: str = ""
    console: bool = True


# --- Scoring / Switching / Monitor / Storage (структура без изменений) ---

_DEFAULT_THRESHOLDS = {
    "ttfb_good": 150, "ttfb_bad": 1500,
    "jitter_good": 15, "jitter_bad": 250,
    "loss_gate": 50,
    "dl_min": 2, "dl_target": 25,
    "throttle_bad": 0.3, "throttle_good": 0.9,
    "hold_min": 5, "hold_target": 20,
    "cv_good": 0.1, "cv_bad": 0.8,
}
_DEFAULT_WEIGHTS = {
    "reliability": 0.35, "consistency": 0.20, "throttle": 0.15,
    "jitter": 0.10, "latency": 0.10, "throughput": 0.10,
}


@dataclass
class ScoringConfig:
    enabled: bool = True
    alpha: float = 0.3
    flap_lambda: float = 0.5
    avail_floor: float = 0.7
    avail_full: float = 0.9
    file: str = "./results/score.csv"
    thresholds: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_THRESHOLDS))
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))


@dataclass
class RotationConfig:
    enabled: bool = True
    interval: float = 10800.0
    jitter: float = 900.0
    min_dwell: float = 1800.0
    top_k: int = 5
    min_score: float = 55.0
    avoid_recent: int = 2


@dataclass
class SwitchingConfig:
    enabled: bool = False
    quality_margin: float = 15.0
    confirm_cycles: int = 2
    comfort_floor: float = 50.0
    cooldown: float = 300.0
    exclude_test_groups: bool = True
    freeze_groups: list[str] = field(default_factory=lambda: ["global-auto-out"])
    state_file: str = "./results/switch_state.json"
    rotation: RotationConfig = field(default_factory=RotationConfig)


@dataclass
class MonitorConfig:
    enabled: bool = False
    interval: float = 20.0
    probe_url: str = "http://cp.cloudflare.com"
    probe_timeout: int = 5000
    fails: int = 2
    reassert_drift: bool = True


@dataclass
class TrafficConfig:
    enabled: bool = True
    poll_interval: float = 5.0
    flush_interval: float = 60.0


@dataclass
class StorageConfig:
    enabled: bool = False
    db_file: str = "./results/stats.db"
    nodes_file: str = "/etc/sing-box-subscribe/nodes.json"
    retention_days: int = 30
    store_results: bool = True
    traffic: TrafficConfig = field(default_factory=TrafficConfig)


@dataclass
class Config:
    clash_api: ClashApiConfig
    testing_groups: list[TestingGroupConfig]
    region_groups: RegionGroupsConfig
    run: RunConfig
    report: ReportConfig
    scoring: ScoringConfig
    switching: SwitchingConfig
    monitor: MonitorConfig
    storage: StorageConfig
    tests: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def testing_group(self) -> TestingGroupConfig:
        """Пока работаем с одной группой (первой). Многогрупповость — позже."""
        return self.testing_groups[0]


# --- Загрузка ------------------------------------------------------------


def _section(data: dict, key: str) -> dict:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Секция '{key}' должна быть объектом")
    return value


def load_config(path: str) -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Конфиг не найден: {path}. Скопируйте config.example.json в config.json."
        )
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Некорректный JSON в {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Корень config.json должен быть объектом")

    _maybe_validate_schema(data, path)   # best-effort: JSON Schema, если есть jsonschema

    tgs = [_load_testing_group(t) for t in (data.get("testing_groups") or [])]
    if not tgs:
        raise ValueError("Не задано ни одной testing_groups")

    cfg = Config(
        clash_api=ClashApiConfig(**_section(data, "clash_api")),
        testing_groups=tgs,
        region_groups=RegionGroupsConfig(**_section(data, "region_groups")),
        run=_load_run(_section(data, "run")),
        report=ReportConfig(**_section(data, "report")),
        scoring=_load_scoring(_section(data, "scoring")),
        switching=_load_switching(_section(data, "switching")),
        monitor=MonitorConfig(**_section(data, "monitor")),
        storage=_load_storage(_section(data, "storage")),
        tests=_section(data, "tests"),
    )
    _validate(cfg)
    return cfg


def _load_testing_group(t: dict) -> TestingGroupConfig:
    return TestingGroupConfig(
        tag=t.get("tag", "main"),
        connection=ConnectionConfig(**(t.get("connection") or {})),
        selector=SelectorConfig(**(t.get("selector") or {})),
    )


def _load_run(run: dict) -> RunConfig:
    default = RunParams(**(run.get("default") or {}))
    group_overrides = {
        spec.get("testing_group_tag", ""): spec.get("default_overrides") or {}
        for spec in (run.get("testing_groups_specifics") or [])
    }
    region_overrides = {
        spec.get("region_group_tag", ""): spec.get("default_overrides") or {}
        for spec in (run.get("region_groups_specifics") or [])
    }
    return RunConfig(default=default, group_overrides=group_overrides,
                     region_overrides=region_overrides)


def _load_storage(st: dict) -> StorageConfig:
    traffic = st.get("traffic") or {}
    cfg = StorageConfig(**{k: v for k, v in st.items() if k != "traffic"})
    cfg.traffic = TrafficConfig(**traffic)
    return cfg


def _load_scoring(sc: dict) -> ScoringConfig:
    cfg = ScoringConfig(**{k: v for k, v in sc.items()
                           if k not in ("thresholds", "weights")})
    cfg.thresholds = {**_DEFAULT_THRESHOLDS, **(sc.get("thresholds") or {})}
    cfg.weights = {**_DEFAULT_WEIGHTS, **(sc.get("weights") or {})}
    return cfg


def _load_switching(sw: dict) -> SwitchingConfig:
    rot = sw.get("rotation") or {}
    cfg = SwitchingConfig(**{k: v for k, v in sw.items() if k != "rotation"})
    cfg.rotation = RotationConfig(**rot)
    return cfg


def _maybe_validate_schema(data: dict, config_path: str) -> None:
    """Проверить конфиг по config.schema.json, если установлен jsonschema.

    На роутере jsonschema может отсутствовать — тогда просто пропускаем (мягко).
    """
    try:
        import jsonschema
    except ImportError:
        return
    schema_path = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                               data.get("$schema", "config.schema.json"))
    if not os.path.exists(schema_path):
        return
    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"Конфиг не соответствует схеме: {exc.message} "
                         f"(путь: {'/'.join(map(str, exc.absolute_path)) or 'корень'})") from exc


def _validate(cfg: Config) -> None:
    tg = cfg.testing_group
    if not tg.selector.group:
        raise ValueError("testing_groups[].selector.group не задан")
    if not (1 <= tg.connection.port <= 65535):
        raise ValueError(f"Некорректный порт: {tg.connection.port}")
    if cfg.region_groups.recognition not in ("by_selector", "parse", "manually"):
        raise ValueError(
            f"region_groups.recognition должен быть by_selector|parse|manually, "
            f"а не {cfg.region_groups.recognition!r}")
    if cfg.report.format not in ("jsonl", "json", "csv"):
        raise ValueError(f"Неизвестный report.format: {cfg.report.format}")
    # Диапазоны (в т.ч. защита от отрицательного retention_days — иначе prune()
    # сдвинул бы cutoff в будущее и удалил всю историю).
    if cfg.storage.retention_days < 0:
        raise ValueError(f"storage.retention_days не может быть < 0: {cfg.storage.retention_days}")
    if cfg.run.default.rounds < 1:
        raise ValueError(f"run.default.rounds должен быть >= 1: {cfg.run.default.rounds}")
    if cfg.monitor.enabled and cfg.monitor.interval <= 0:
        raise ValueError("monitor.interval должен быть > 0")
