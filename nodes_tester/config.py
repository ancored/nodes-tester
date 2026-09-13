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
      parse    — коарс-регион из страны в имени ноды (eu/us/ru/other); дефолт,
                 подходит для плоского nodes-tester;
      manually — явные списки регион→теги нод (см. list).
    """
    enabled: bool = True
    recognition: str = "parse"
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

_DEFAULT_TESTS = ["connectivity", "latency", "jitter", "download", "reachability"]
# Тесты, дающие scoring-компоненты (connectivity — только gate, компонента нет;
# heavy_download — veto, в score не входит). Набор без пересечения с этим → score 0.
_SCORING_TESTS = {"latency", "jitter", "download", "reachability"}


@dataclass
class RunParams:
    tests_enabled: list[str] = field(default_factory=lambda: list(_DEFAULT_TESTS))
    loop: bool = True
    # rotation_bound: не гонять тесты непрерывно, а просыпаться к ближайшему сроку
    # ротации (switcher.rotate_deadline) и делать один прогон. Emergency между
    # прогонами ловит монитор. Работает только при loop + switching.rotation.enabled;
    # иначе игнорируется (обычный цикл с pass_pause).
    rotation_bound: bool = True
    pass_pause: float = 0.0                 # пауза между прогонами (в rotation_bound не нужна)
    switch_delay: float = 1.0
    # min_host_gap: анти-ТСПУ. Минимум секунд между обращениями к ОДНОМУ хосту с
    # РАЗНЫМ (порт/протокол). Тот же хост+порт+протокол (та же нода) ожидания не ждёт.
    # Обход нод строится так, чтобы одинаковые хосты стояли максимально далеко; зазор —
    # «пол» по времени на случай малого числа хостов. Групповых/межпрогонных пауз нет.
    min_host_gap: float = 120.0
    rounds: int = 1
    request_timeout: float = 10.0
    restore_selection: bool = True
    # Двухуровневое тестирование: лёгкие тесты (tests_enabled, вкл. 10МБ download)
    # скорят ВСЕ ноды; затем тяжёлый 50МБ download гоняется как pass/fail veto только
    # для heavy_candidates лучших нод региона (+ активная). 0 = двухуровневость выкл.
    heavy_candidates: int = 0
    heavy_veto_hours: float = 6.0          # сколько держится veto, пока не пере-проверим

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
class LoadBalanceConfig:
    """Балансировка выбора активной ноды по ОСЯМ (провайдер/страна/протокол).
    По каждой оси со strength>0: (1) fair-share — вес провайдера НЕ зависит от числа
    его нод (100-нодовый и 10-нодовый получают равную вероятность, а не 10:1);
    (2) underuse — перегруженная по трафику ось дополнительно придавливается.
    strength=1 → равномерно по оси; 0 → пропорционально числу нод (ось выкл.); протокол
    обычно мягко (0.3). Влияет на выбор ротации И emergency; базовый score не трогается."""
    enabled: bool = False
    window_hours: float = 24.0
    provider_strength: float = 1.0
    country_strength: float = 1.0
    protocol_strength: float = 0.3


@dataclass
class RotationConfig:
    enabled: bool = True
    interval: float = 10800.0
    jitter: float = 900.0
    min_dwell: float = 1800.0
    top_k: int = 5
    min_score: float = 55.0
    avoid_recent: int = 2
    load_balance: LoadBalanceConfig = field(default_factory=LoadBalanceConfig)


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
class CooldownConfig:
    """Единый ВРЕМЕННОЙ backoff для нод, проваливших gate. После каждого подряд
    провала нода не тестируется `base_seconds · 2^(n-1)` секунд (удвоение), с потолком
    `garbage_hours`. Дойдя до потолка — считается МУСОРНОЙ (карантин `garbage_hours`).
    Успешный gate снимает backoff. Состояние персистентно (переживает рестарт) и
    измеряется ВРЕМЕНЕМ, а не номерами прогонов — это совместимо с rotation_bound, где
    проходов в сутки мало и суточный pass_no сбрасывается (иначе pass-based cooldown
    залипал бы, см. review.md P1). `max_skip` — верхняя граница числа удвоений."""
    enabled: bool = True
    base_seconds: float = 600.0
    max_skip: int = 32
    garbage_hours: float = 72.0


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
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
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


def _filtered(cls, data: dict) -> dict:
    """Только поля датакласса cls — терпимость к неизвестным/устаревшим ключам."""
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in known}


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
        cooldown=CooldownConfig(**_filtered(CooldownConfig, _section(data, "cooldown"))),
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
    # Терпимость к неизвестным/устаревшим ключам (напр. удалённый group_pause):
    # берём только поля RunParams, чтобы старые конфиги не роняли загрузку.
    known = {f.name for f in dataclasses.fields(RunParams)}
    default = RunParams(**{k: v for k, v in (run.get("default") or {}).items() if k in known})
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
    lb = rot.get("load_balance") or {}
    cfg = SwitchingConfig(**{k: v for k, v in sw.items() if k != "rotation"})
    cfg.rotation = RotationConfig(**{k: v for k, v in rot.items() if k != "load_balance"})
    cfg.rotation.load_balance = LoadBalanceConfig(**lb)
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
    if cfg.region_groups.recognition not in ("parse", "manually"):
        raise ValueError(
            f"region_groups.recognition должен быть parse|manually, "
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
    # Предупреждение: набор тестов без scoring-компонента даёт всем нодам score 0
    # (напр. только connectivity — он лишь gate). Тогда candidates() пуст → нет выбора.
    excl = {r.lower() for r in cfg.region_groups.exclude}
    if not (set(cfg.run.default.tests_enabled) & _SCORING_TESTS):
        print("  [config] ВНИМАНИЕ: run.default.tests_enabled без scoring-теста "
              f"(нужен один из {sorted(_SCORING_TESTS)}) → score будет 0")
    for tag in cfg.run.region_overrides:
        if tag.lower() in excl:
            continue                          # регион не тестируется — не предупреждаем
        eff = cfg.run.for_region(cfg.run.default, tag).tests_enabled
        if not (set(eff) & _SCORING_TESTS):
            print(f"  [config] ВНИМАНИЕ: регион '{tag}' tests_enabled={eff} без scoring-"
                  f"теста → score 0, ноды региона не станут кандидатами")
