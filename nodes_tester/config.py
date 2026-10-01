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
class BoxApiConfig:
    """API-сервис sing-box 1.14 (`services[].type = "api"` в конфиге sing-box)."""
    url: str = "http://127.0.0.1:9090"
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
# Обязательные тесты групп (run.*.required_tests): отдельная фаза, в рейтинг не входят.
REQUIRED_TESTS = {"gemini"}


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
    # для всего пула ротации (switching.rotation.top_k) и только в регионах, где в этом
    # прогоне наступает ротация. > 0 включает, 0 = двухуровневость выкл.
    heavy_candidates: int = 0
    heavy_veto_hours: float = 6.0          # сколько держится veto, пока не пере-проверим
    # Обязательные тесты группы (напр. ["gemini"] для ai): в группе может быть активной
    # только нода со свежим успешным результатом; никто не прошёл — группа на failsafe.
    # Задаётся для групп через run.node_groups_specifics; гоняются отдельной фазой.
    required_tests: list[str] = field(default_factory=list)

    def merged(self, overrides: dict) -> "RunParams":
        """Копия с наложенными override'ами (мелкий мердж по известным полям)."""
        fields = {f.name for f in dataclasses.fields(self)}
        patch = {k: v for k, v in (overrides or {}).items() if k in fields}
        return dataclasses.replace(self, **patch)


@dataclass
class RunConfig:
    default: RunParams = field(default_factory=RunParams)
    group_overrides: dict[str, dict] = field(default_factory=dict)   # testing_group_tag -> overrides
    # Группа нод ({name}-auto-out) -> overrides (run.node_groups_specifics; до 0.3.0 —
    # region_groups_specifics с тегом региона, это те же имена групп eu/us/other).
    node_group_overrides: dict[str, dict] = field(default_factory=dict)

    def for_group(self, group_tag: str) -> RunParams:
        return self.default.merged(self.group_overrides.get(group_tag, {}))

    def for_node_groups(self, base: RunParams, groups) -> RunParams:
        """Параметры ноды из её групп: переопределения по порядку групп (позднее
        побеждает), наборы тестов объединяются."""
        params, tests = base, list(base.tests_enabled)
        for name in groups:
            over = self.node_group_overrides.get(name)
            if not over:
                continue
            params = params.merged(over)
            tests += [t for t in over.get("tests_enabled", []) if t not in tests]
        return dataclasses.replace(params, tests_enabled=tests) if params is not base else base


@dataclass
class ReportConfig:
    # Файловый репортер убран — сырые результаты живут в SQLite. Осталась только
    # печать пер-нодной сводки в консоль.
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
    # align: сроки ротации всех групп — на общей сетке слотов (шаг interval, jitter общий
    # на слот) → один прогон тестера обслуживает все группы. False — у каждой группы
    # свой срок (now + interval ± jitter).
    align: bool = True
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
    rotation: RotationConfig = field(default_factory=RotationConfig)


@dataclass
class MonitorConfig:
    enabled: bool = False
    interval: float = 20.0           # период тика монитора, сек
    reassert_drift: bool = True      # пере-выставлять выбор селекторов при дрейфе
    # --- Здоровье активной ноды: трафик-гейт + внеплановый зонд ---
    # Идёт боевой трафик (≥ silence_floor за окно) → нода заведомо жива, не трогаем.
    # «Тихо» дольше silence_window → внеплановый зонд (закачка ~probe_bytes через
    # socks). Провал (нет ответа ИЛИ скорость < probe_min_mbps — так ловится throttle
    # ТСПУ, который delay-проба пропускает) fails раз подряд → EMERGENCY.
    silence_window: float = 300.0    # сек тишины до внепланового зонда
    silence_floor_mb: float = 0.0    # МБ за окно = «трафик идёт»; 0 = авто (min_mbps×window)
    probe_bytes: int = 1_000_000     # целевой объём закачки зонда, байт (~1 МБ)
    probe_min_mbps: float = 1.0      # ниже порога → throttle → провал зонда
    probe_timeout: float = 8.0       # предел закачки/коннекта зонда, сек
    probe_url: str = ""              # пусто = speed.cloudflare.com c probe_bytes
    fails: int = 2                   # провалов зонда подряд → emergency

    @property
    def silence_floor_bytes(self) -> int:
        """Порог «трафик идёт» в байтах за окно. 0 → авто: min_mbps × window."""
        if self.silence_floor_mb and self.silence_floor_mb > 0:
            return int(self.silence_floor_mb * 1_000_000)
        return int(self.probe_min_mbps * 1_000_000 / 8 * self.silence_window)


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
    """Пауза (backoff) и карантин нод, проваливших gate.

    Пауза считается в ПРОГОНАХ: после n-го подряд провала нода пропускает следующие
    2^(n-1) прогонов (1, 2, 4, 8, 16, …). Когда пропуск дорастает до `max_skip`, нода
    уходит в КАРАНТИН — он считается во ВРЕМЕНИ: `garbage_hours` часов, затем одна проба
    (провал → снова карантин). Успешный gate снимает и паузу, и карантин.

    Счётчик прогонов для паузы — СКВОЗНОЙ (meta.pass_seq, не сбрасывается в полночь),
    поэтому нет залипания из review.md P1 (там сравнивался посуточный pass_no).
    Состояние персистентно (таблица garbage), переживает рестарт.
    `base_seconds` — устаревший ключ временной модели, игнорируется.

    `low_score` > 0 — карантин и для живых, но слабых нод: рейтинг (сглаженный) ниже
    порога → сразу карантин на `garbage_hours`. Не трогает активные ноды и новые
    (меньше 3 замеров). После карантина — одна проба: рейтинг поднялся до порога →
    нода возвращается, иначе снова карантин. 0 — выключено."""
    enabled: bool = True
    max_skip: int = 32
    garbage_hours: float = 72.0
    low_score: float = 0.0


@dataclass
class DashboardConfig:
    """Веб-админка/дашборд. `enabled` — встраивать ли сервер в процесс тестера
    (тогда доступны write/control-эндпоинты поверх живого Runner). Отдельный
    `python -m dashboard` работает независимо от этого флага (read-only режим).

    Доступ: просмотр (`/api/*` read) открыт при `read_open`; write/control требуют
    заголовок `X-Admin-Token`, совпадающий с `token`. Пустой `token` полностью
    отключает write/control (безопасный дефолт). `host` привязывает сокет к
    интерфейсу — держите LAN-адрес, не выставляйте наружу.

    `providers_file` — подписки, которые правит редактор (тот же файл, что читает
    nodes_fetch, напр. `/root/nodes-data/config-main/providers.json`); относительный
    путь — от папки config.json, пусто = `providers.json` рядом с config.json."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8088
    interval: int = 10               # период авто-обновления UI, сек
    token: str = ""                  # shared-токен для write/control ("" = write выкл.)
    read_open: bool = True           # просмотр без токена
    providers_file: str = ""         # подписки для редактора ("" = рядом с config.json)
    groups_file: str = ""            # "" = groups_params.json рядом с providers_file


@dataclass
class IpInfoConfig:
    """Тип выходного IP (nodes_tester.ipinfo): ASN, дата-центр/мобильная сеть/прокси по
    exit_ip из connectivity. Пакетный запрос к `url` (ip-api.com) — через SOCKS тестера на
    лучшей здоровой ноде (`via_tester`), при неудаче напрямую; кэш ttl_days."""
    enabled: bool = True
    url: str = "http://ip-api.com/batch"
    ttl_days: float = 7.0
    timeout: float = 10.0
    via_tester: bool = True          # запрос через SOCKS тестера (обход DNS-фильтра роутера)


@dataclass
class SingboxControlConfig:
    """Остановка/запуск sing-box из админки и управление службой killswitch роутера
    (nodes_tester.singbox_ctl). Работает только на OpenWrt (есть `init`)."""
    enabled: bool = True
    init: str = "/etc/init.d/sing-box"
    stop_flag: str = "/var/run/nodes-tester/singbox-stopped"
    killswitch_init: str = "/etc/init.d/killswitch"
    killswitch_table: str = "killswitch"     # таблица inet, которую загружает служба
    interval: float = 5.0            # период сторожа, с
    down_alert: float = 60.0         # уведомить, если sing-box не работает дольше, с


@dataclass
class TelegramConfig:
    token: str = ""                  # токен бота (@BotFather)
    chat_id: str = ""                # куда писать: свой id или id группы


@dataclass
class WebhookConfig:
    url: str = ""                    # POST JSON {event, text, ts}


@dataclass
class NotifyConfig:
    """Уведомления (nodes_tester.notify): Telegram и/или webhook. Без получателей и при
    enabled=false не отправляется ничего. `events` — какие события слать; переключения
    нод — только с причинами из `switch_reasons` (ротация каждые часы — шумно)."""
    enabled: bool = False
    name: str = ""                   # подпись роутера в начале сообщения
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    proxy: str = ""                  # напр. socks5h://… — если Telegram без прокси недоступен
    events: list[str] = field(default_factory=lambda: [
        "switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox"])
    switch_reasons: list[str] = field(default_factory=lambda: ["emergency", "emergency-stuck"])
    expiry_days: float = 3.0         # предупреждать, когда до конца подписки ≤ стольких дней
    dedupe_minutes: float = 60.0     # не повторять одинаковое сообщение чаще
    max_per_hour: int = 20           # общий предел (защита от шквала)
    timeout: float = 10.0


@dataclass
class Config:
    box_api: BoxApiConfig
    testing_groups: list[TestingGroupConfig]
    region_groups: RegionGroupsConfig
    run: RunConfig
    report: ReportConfig
    scoring: ScoringConfig
    switching: SwitchingConfig
    monitor: MonitorConfig
    storage: StorageConfig
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    ip_info: IpInfoConfig = field(default_factory=IpInfoConfig)
    singbox_control: SingboxControlConfig = field(default_factory=SingboxControlConfig)
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


def _load_box_api(data: dict) -> BoxApiConfig:
    """box_api; старый clash_api (до 0.3.0) читается как адрес того же порта API-сервиса."""
    if "box_api" in data:
        return BoxApiConfig(**_filtered(BoxApiConfig, _section(data, "box_api")))
    legacy = _section(data, "clash_api")
    if legacy:
        print("  [config] секция clash_api устарела: переименуйте её в box_api "
              "(base_url → url); тестер работает через API-сервис sing-box 1.14")
    return BoxApiConfig(url=legacy.get("base_url", BoxApiConfig.url),
                        secret=legacy.get("secret", ""),
                        timeout=legacy.get("timeout", BoxApiConfig.timeout))


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
        box_api=_load_box_api(data),
        testing_groups=tgs,
        region_groups=RegionGroupsConfig(**_section(data, "region_groups")),
        run=_load_run(_section(data, "run")),
        report=ReportConfig(**_filtered(ReportConfig, _section(data, "report"))),
        scoring=_load_scoring(_section(data, "scoring")),
        switching=_load_switching(_section(data, "switching")),
        monitor=MonitorConfig(**_filtered(MonitorConfig, _section(data, "monitor"))),
        storage=_load_storage(_section(data, "storage")),
        cooldown=CooldownConfig(**_filtered(CooldownConfig, _section(data, "cooldown"))),
        dashboard=DashboardConfig(**_filtered(DashboardConfig, _section(data, "dashboard"))),
        notify=_load_notify(_section(data, "notify")),
        ip_info=IpInfoConfig(**_filtered(IpInfoConfig, _section(data, "ip_info"))),
        singbox_control=SingboxControlConfig(**_filtered(SingboxControlConfig,
                                                         _section(data, "singbox_control"))),
        tests=_section(data, "tests"),
    )
    _validate(cfg)
    # Абсолютный путь к config.json — нужен админке (редактор конфигов, схема рядом).
    cfg.path = os.path.abspath(path)
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
    node_group_overrides = {
        spec.get("region_group_tag", ""): spec.get("default_overrides") or {}
        for spec in (run.get("region_groups_specifics") or [])
    }
    node_group_overrides.update({
        spec.get("group", ""): spec.get("default_overrides") or {}
        for spec in (run.get("node_groups_specifics") or [])
    })
    return RunConfig(default=default, group_overrides=group_overrides,
                     node_group_overrides=node_group_overrides)


def _load_storage(st: dict) -> StorageConfig:
    traffic = st.get("traffic") or {}
    cfg = StorageConfig(**{k: v for k, v in st.items() if k != "traffic"})
    cfg.traffic = TrafficConfig(**traffic)
    return cfg


def _load_notify(nt: dict) -> NotifyConfig:
    cfg = NotifyConfig(**_filtered(NotifyConfig, {k: v for k, v in nt.items()
                                                  if k not in ("telegram", "webhook")}))
    cfg.telegram = TelegramConfig(**_filtered(TelegramConfig, nt.get("telegram") or {}))
    cfg.webhook = WebhookConfig(**_filtered(WebhookConfig, nt.get("webhook") or {}))
    cfg.telegram.chat_id = str(cfg.telegram.chat_id or "")
    return cfg


def _load_scoring(sc: dict) -> ScoringConfig:
    cfg = ScoringConfig(**_filtered(ScoringConfig, {k: v for k, v in sc.items()
                                                    if k not in ("thresholds", "weights")}))
    cfg.thresholds = {**_DEFAULT_THRESHOLDS, **(sc.get("thresholds") or {})}
    cfg.weights = {**_DEFAULT_WEIGHTS, **(sc.get("weights") or {})}
    return cfg


def _load_switching(sw: dict) -> SwitchingConfig:
    rot = sw.get("rotation") or {}
    lb = rot.get("load_balance") or {}
    cfg = SwitchingConfig(**_filtered(SwitchingConfig, {k: v for k, v in sw.items()
                                                        if k != "rotation"}))
    cfg.rotation = RotationConfig(**{k: v for k, v in rot.items() if k != "load_balance"})
    cfg.rotation.load_balance = LoadBalanceConfig(**lb)
    return cfg


def _maybe_validate_schema(data: dict, config_path: str) -> None:
    """Проверить конфиг по config.schema.json, если установлен jsonschema.

    Явная ссылка `$schema` обязана существовать: молчаливый пропуск превращал
    редактор админки в обход валидации из-за одной опечатки в имени файла.
    """
    schema_name = data.get("$schema", "config.schema.json")
    schema_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), schema_name)
    if not os.path.exists(schema_path):
        if "$schema" in data:
            raise ValueError(f"Файл JSON Schema не найден: {schema_path}")
        return
    try:
        import jsonschema
    except ImportError as exc:
        raise ValueError(
            "Для проверки config.schema.json установите зависимость jsonschema"
        ) from exc
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
    # Рейтинг и состояние переключений теперь живут в SQLite — без storage негде
    # хранить scores/switch_state/историю. Требуем storage при scoring/switching.
    if (cfg.scoring.enabled or cfg.switching.enabled) and not cfg.storage.enabled:
        raise ValueError("scoring/switching хранят состояние в БД — включите storage.enabled")
    # Диапазоны (в т.ч. защита от отрицательного retention_days — иначе prune()
    # сдвинул бы cutoff в будущее и удалил всю историю).
    if cfg.storage.retention_days < 0:
        raise ValueError(f"storage.retention_days не может быть < 0: {cfg.storage.retention_days}")
    if cfg.run.default.rounds < 1:
        raise ValueError(f"run.default.rounds должен быть >= 1: {cfg.run.default.rounds}")
    if cfg.monitor.enabled and cfg.monitor.interval <= 0:
        raise ValueError("monitor.interval должен быть > 0")
    # Эти поля используются напрямую сетевым сервером/таймерами. Проверяем
    # семантически даже когда необязательный пакет jsonschema на роутере отсутствует.
    if type(cfg.dashboard.port) is not int or not 1 <= cfg.dashboard.port <= 65535:
        raise ValueError(f"dashboard.port должен быть целым числом 1..65535: {cfg.dashboard.port!r}")
    if type(cfg.dashboard.interval) is not int or cfg.dashboard.interval <= 0:
        raise ValueError(f"dashboard.interval должен быть положительным целым: {cfg.dashboard.interval!r}")
    if not isinstance(cfg.dashboard.host, str) or not cfg.dashboard.host.strip():
        raise ValueError("dashboard.host должен быть непустой строкой")
    if type(cfg.dashboard.enabled) is not bool or type(cfg.dashboard.read_open) is not bool:
        raise ValueError("dashboard.enabled/read_open должны быть boolean")
    if not isinstance(cfg.dashboard.token, str):
        raise ValueError("dashboard.token должен быть строкой")
    for key in ("providers_file", "groups_file"):
        if not isinstance(getattr(cfg.dashboard, key), str):
            raise ValueError(f"dashboard.{key} должен быть строкой")
    from .notify import EVENTS, SWITCH_REASONS
    nt = cfg.notify
    unknown = set(nt.events) - set(EVENTS)
    if unknown:
        raise ValueError(f"notify.events: неизвестные события {', '.join(sorted(unknown))} "
                         f"(доступны: {', '.join(EVENTS)})")
    unknown = set(nt.switch_reasons) - set(SWITCH_REASONS)
    if unknown:
        raise ValueError(f"notify.switch_reasons: неизвестные причины {', '.join(sorted(unknown))}")
    if nt.webhook.url and not nt.webhook.url.startswith(("http://", "https://")):
        raise ValueError("notify.webhook.url должен начинаться с http:// или https://")
    # Предупреждение: набор тестов без scoring-компонента даёт всем нодам score 0
    # (напр. только connectivity — он лишь gate). Тогда candidates() пуст → нет выбора.
    excl = {r.lower() for r in cfg.region_groups.exclude}
    if not (set(cfg.run.default.tests_enabled) & _SCORING_TESTS):
        print("  [config] ВНИМАНИЕ: run.default.tests_enabled без scoring-теста "
              f"(нужен один из {sorted(_SCORING_TESTS)}) → score будет 0")
    for tag, over in [("default", {"required_tests": cfg.run.default.required_tests}),
                      *cfg.run.node_group_overrides.items()]:
        unknown = set(over.get("required_tests") or []) - REQUIRED_TESTS
        if unknown:
            raise ValueError(f"required_tests группы '{tag}': неизвестные тесты "
                             f"{', '.join(sorted(unknown))} (доступны: {', '.join(sorted(REQUIRED_TESTS))})")
    for tag in cfg.run.node_group_overrides:
        if tag.lower() in excl:
            continue                          # регион не тестируется — не предупреждаем
        eff = cfg.run.for_node_groups(cfg.run.default, [tag]).tests_enabled
        if not (set(eff) & _SCORING_TESTS):
            print(f"  [config] ВНИМАНИЕ: группа '{tag}' tests_enabled={eff} без scoring-"
                  f"теста → score 0, ноды группы не станут кандидатами")
