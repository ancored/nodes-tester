"""Единый config.json: тестер, группы, проверки, подписки, конвейер, админка, уведомления.

Формат описан в docs/CONFIG.md. Как у sing-box: разделы по сущностям, сущности — массивы с
`tag`, длительности строками ("5m", "24h"), значения по умолчанию в файл не пишутся,
неизвестное поле — ошибка с путём (`groups[1].requre: неизвестное поле`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from nodes_common.units import parse_duration


class ConfigError(ValueError):
    """Ошибка конфига с путём поля."""


# --- Чтение полей ----------------------------------------------------------


def _fields(obj, path: str, allowed) -> dict:
    """Объект раздела; неизвестные поля — ошибка (опечатки не проходят молча)."""
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise ConfigError(f"{path or 'корень'}: ожидается объект")
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        where = f"{path}." if path else ""
        raise ConfigError(f"{where}{unknown[0]}: неизвестное поле (есть: {', '.join(sorted(allowed))})")
    return obj


def _mapping(obj, path: str) -> dict:
    """Объект с произвольными ключами (регионы, имена)."""
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise ConfigError(f"{path}: ожидается объект")
    return obj


def _duration(obj: dict, key: str, path: str, default: float) -> float:
    if key not in obj:
        return default
    try:
        return parse_duration(obj[key])
    except ValueError as exc:
        raise ConfigError(f"{path}.{key}: {exc}") from None


def _number(obj: dict, key: str, path: str, default, low=None, high=None, integer=False):
    if key not in obj:
        return default
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
        raise ConfigError(f"{path}.{key}: ожидается {'целое ' if integer else ''}число, получено {value!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError(f"{path}.{key}: {value} вне диапазона {low}..{high}")
    return value


def _bool(obj: dict, key: str, path: str, default: bool) -> bool:
    value = obj.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{key}: ожидается true или false, получено {value!r}")
    return value


def _string(obj: dict, key: str, path: str, default: str) -> str:
    value = obj.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{path}.{key}: ожидается строка, получено {value!r}")
    return value


def _strings(obj: dict, key: str, path: str, default=()) -> list[str]:
    value = obj.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{path}.{key}: ожидается список строк")
    return list(value)


# --- Разделы ---------------------------------------------------------------


@dataclass
class ApiConfig:
    """API-сервис sing-box 1.14 (`services[].type = "api"` в конфиге sing-box)."""
    url: str = "http://127.0.0.1:9090"
    secret: str = ""
    timeout: float = 5.0


def _api(obj) -> ApiConfig:
    path = "api"
    obj = _fields(obj, path, ("url", "secret", "timeout"))
    return ApiConfig(url=_string(obj, "url", path, ApiConfig.url),
                     secret=_string(obj, "secret", path, ""),
                     timeout=_duration(obj, "timeout", path, 5.0))


@dataclass
class TesterConfig:
    socks: str = "socks5h://127.0.0.1:2080"   # socks5h: DNS резолвит выбранная нода
    selector: str = "nodes-tester"
    # Проход к сроку ротации (монитор ловит аварии между проходами); false — проходы
    # подряд с паузой `pause`.
    follow_rotation: bool = True
    pause: float = 0.0
    switch_delay: float = 1.0
    request_timeout: float = 10.0
    # Анти-ТСПУ: не чаще раза в host_gap к одному хосту с другим портом или протоколом.
    host_gap: float = 120.0


def _tester(obj) -> TesterConfig:
    path, d = "tester", TesterConfig()
    obj = _fields(obj, path, ("socks", "selector", "follow_rotation", "pause", "switch_delay",
                              "request_timeout", "host_gap"))
    socks = _string(obj, "socks", path, d.socks)
    if not re.fullmatch(r"socks5h?://([^@/]+@)?[^:/]+:\d+", socks):
        raise ConfigError(f"{path}.socks: ожидается socks5h://хост:порт, получено {socks!r}")
    return TesterConfig(socks=socks, selector=_string(obj, "selector", path, d.selector),
                        follow_rotation=_bool(obj, "follow_rotation", path, d.follow_rotation),
                        pause=_duration(obj, "pause", path, d.pause),
                        switch_delay=_duration(obj, "switch_delay", path, d.switch_delay),
                        request_timeout=_duration(obj, "request_timeout", path, d.request_timeout),
                        host_gap=_duration(obj, "host_gap", path, d.host_gap))


# Замеры рейтинга при каждом проходе ноды и их параметры по умолчанию.
TESTS = ("connectivity", "latency", "jitter", "download", "reachability")
_TEST_FIELDS = {
    "connectivity": ("service", "timeout"),
    "latency": ("url", "timeout"),
    "jitter": ("url", "samples", "interval", "timeout"),
    "download": ("url", "duration", "window_mb", "timeout"),
    "reachability": ("urls", "timeout", "workers"),
}
_DURATION_KEYS = ("timeout", "interval", "duration", "ttl")


def _options(obj: dict, path: str, allowed) -> dict:
    """Параметры теста или проверки; длительности — в секунды, for_region — по регионам."""
    obj = _fields(obj, path, (*allowed, "for_region"))
    out = {k: (_duration(obj, k, path, 0.0) if k in _DURATION_KEYS else v)
           for k, v in obj.items() if k != "for_region"}
    regions = _mapping(obj.get("for_region"), f"{path}.for_region")
    if regions:
        out["for_region"] = {name: _options(over, f"{path}.for_region.{name}", allowed)
                             for name, over in regions.items()}
    return out


def _tests(obj) -> dict[str, dict]:
    path = "tests"
    obj = _fields(obj, path, (*TESTS, "for_region"))
    out = {name: _options(obj.get(name) or {}, f"{path}.{name}", _TEST_FIELDS[name]) for name in TESTS}
    for region, over in _mapping(obj.get("for_region"), f"{path}.for_region").items():
        over = _fields(over, f"{path}.for_region.{region}", TESTS)
        for name, opts in over.items():
            region_opts = _options(opts, f"{path}.for_region.{region}.{name}", _TEST_FIELDS[name])
            out[name].setdefault("for_region", {})[region] = region_opts
    return out


# Проверки: вердикт «да / нет / неизвестно» со сроком годности ttl.
_CHECK_DEFAULTS = {
    "country": {"ttl": 86400.0},
    "ip_type": {"ttl": 86400.0, "url": "http://ip-api.com/batch", "timeout": 10.0, "via_tester": True},
    "geo_consistent": {"ttl": 86400.0},
    "gemini": {"ttl": 86400.0, "attempts": 3, "timeout": 20.0},
    "openai": {"ttl": 86400.0, "attempts": 3, "timeout": 15.0},
    "anthropic": {"ttl": 86400.0, "attempts": 3, "timeout": 15.0},
    "heavy": {"ttl": 21600.0, "url": "https://speed.cloudflare.com/__down?bytes=20000000",
              "duration": 30.0, "window_mb": 2},
    "whitelist": {"ttl": 86400.0, "ip_list": "https://github.com/ancored/ru-mobile-whitelist/releases/latest/download/ru-mobile-whitelist-ip.txt",
                  "sni_list": "https://github.com/ancored/ru-mobile-whitelist/releases/latest/download/ru-mobile-whitelist-sni.txt", "dns": "dns-ru", "timeout": 10.0},
}
CHECKS = tuple(_CHECK_DEFAULTS)
# Параметры проверки внутри группы (require/prefer): условия отбора.
_GROUP_CHECK_FIELDS = {
    "country": ("ttl", "countries", "regions", "exclude_countries", "exclude_regions"),
    "ip_type": ("ttl", "kinds"),
}
IP_KINDS = ("residential", "mobile", "hosting", "proxy")


def _checks(obj) -> dict[str, dict]:
    path = "checks"
    obj = _fields(obj, path, CHECKS)
    out = {}
    for name, defaults in _CHECK_DEFAULTS.items():
        given = _options(obj.get(name) or {}, f"{path}.{name}", defaults)
        out[name] = {**defaults, **given}
    return out


@dataclass
class GroupConfig:
    """Группа sing-box `{tag}-auto-out`: в ней все ноды, активную выбирает тестер по проверкам."""
    tag: str
    strict: bool = False            # без кандидата — block, а не failsafe-urltest
    direct_fallback: bool = False   # в крайнем случае — direct-out
    in_global: bool = True          # входит в global-auto-out
    reserve: int = 3
    require: dict[str, dict] = field(default_factory=dict)   # проверка → условия
    prefer: dict[str, dict] = field(default_factory=dict)


_TAG = re.compile(r"[a-z0-9][a-z0-9_]*")


def _group_checks(obj, path: str, checks: dict) -> dict[str, dict]:
    obj = _fields(obj, path, CHECKS)
    out = {}
    for name, params in obj.items():
        allowed = _GROUP_CHECK_FIELDS.get(name, ("ttl",))
        params = _options(params or {}, f"{path}.{name}", allowed)
        if "for_region" in params:
            raise ConfigError(f"{path}.{name}.for_region: в группе не задаётся")
        for key in ("countries", "exclude_countries"):
            values = _strings(params, key, f"{path}.{name}")
            bad = [v for v in values if not re.fullmatch(r"[A-Za-z]{2}", v)]
            if bad:
                raise ConfigError(f"{path}.{name}.{key}: {bad[0]!r} — нужен двухбуквенный код ISO")
            if values:
                params[key] = [v.lower() for v in values]
        kinds = _strings(params, "kinds", f"{path}.{name}")
        if [k for k in kinds if k not in IP_KINDS]:
            raise ConfigError(f"{path}.{name}.kinds: допустимы {', '.join(IP_KINDS)}")
        out[name] = {"ttl": checks[name]["ttl"], **params}
    return out


def _groups(obj, checks: dict, regions: dict, reserve: int) -> list[GroupConfig]:
    if obj is None:
        obj = [{"tag": "all"}]
    if not isinstance(obj, list):
        raise ConfigError("groups: ожидается список групп")
    out, seen = [], set()
    for i, item in enumerate(obj):
        path = f"groups[{i}]"
        item = _fields(item, path, ("tag", "strict", "direct_fallback", "in_global", "reserve",
                                    "require", "prefer"))
        tag = item.get("tag")
        if not isinstance(tag, str) or not _TAG.fullmatch(tag) or tag in ("global", "nodes", "failsafe"):
            raise ConfigError(f"{path}.tag: латиница в нижнем регистре, цифры и _; не global, nodes, "
                              f"failsafe (получено {tag!r})")
        if tag in seen:
            raise ConfigError(f"{path}.tag: группа {tag!r} уже есть")
        seen.add(tag)
        group = GroupConfig(tag=tag, strict=_bool(item, "strict", path, False),
                            direct_fallback=_bool(item, "direct_fallback", path, False),
                            in_global=_bool(item, "in_global", path, True),
                            reserve=_number(item, "reserve", path, reserve, 0, 20, integer=True),
                            require=_group_checks(item.get("require"), f"{path}.require", checks),
                            prefer=_group_checks(item.get("prefer"), f"{path}.prefer", checks))
        for part in ("require", "prefer"):
            for key in ("regions", "exclude_regions"):
                unknown = [r for r in getattr(group, part).get("country", {}).get(key, [])
                           if r not in regions]
                if unknown:
                    raise ConfigError(f"{path}.{part}.country.{key}: неизвестный регион {unknown[0]!r} "
                                      f"(есть: {', '.join(sorted(regions))})")
        out.append(group)
    return out


# Регионы — именованные списки стран. other — страны вне eu/us/ru, если не задан явно.
EUROPE = ("al", "ad", "am", "at", "az", "by", "be", "ba", "bg", "hr", "cy", "cz", "dk", "ee",
          "fi", "fr", "ge", "de", "gr", "hu", "is", "ie", "it", "xk", "lv", "li", "lt", "lu",
          "mt", "md", "mc", "me", "nl", "mk", "no", "pl", "pt", "ro", "sm", "rs", "sk", "si",
          "es", "se", "ch", "tr", "ua", "gb", "va")
BUILTIN_REGIONS = {"eu": list(EUROPE), "us": ["us"], "ru": ["ru"]}


def _regions(obj) -> dict[str, list[str]]:
    path = "regions"
    obj = _mapping(obj, path)
    out = {name: list(ccs) for name, ccs in BUILTIN_REGIONS.items()}
    for name, ccs in obj.items():
        if not _TAG.fullmatch(str(name)):
            raise ConfigError(f"{path}.{name}: недопустимое имя региона")
        out[name] = [c.lower() for c in _strings(obj, name, path)]
    out.setdefault("other", [])          # пустой — значит «всё, что не eu/us/ru»
    return out


def region_of(country: str, regions: dict[str, list[str]]) -> set[str]:
    """Регионы страны; other — если страна не попала в eu, us и ru (и other не задан списком)."""
    cc = (country or "").lower()
    found = {name for name, ccs in regions.items() if cc in ccs}
    if cc and not regions.get("other") and not found & {"eu", "us", "ru"}:
        found.add("other")
    return found


@dataclass
class RatingConfig:
    alpha: float = 0.3
    flap_lambda: float = 0.5
    avail_floor: float = 0.7
    avail_full: float = 0.9
    # Пороги во внутренних единицах scoring: мс, проценты, Мбит/с, доли.
    thresholds: dict[str, float] = field(default_factory=lambda: dict(_THRESHOLDS))
    weights: dict[str, float] = field(default_factory=lambda: dict(_WEIGHTS))


_THRESHOLDS = {"ttfb_good": 150, "ttfb_bad": 1500, "jitter_good": 15, "jitter_bad": 250,
               "loss_gate": 50, "dl_min": 2, "dl_target": 25, "throttle_bad": 0.3,
               "throttle_good": 0.9, "cv_good": 0.1, "cv_bad": 0.8}
_WEIGHTS = {"reliability": 0.35, "consistency": 0.20, "throttle": 0.15,
            "jitter": 0.10, "latency": 0.10, "throughput": 0.10}


def _pair(obj: dict, key: str, path: str, names, as_ms: bool) -> tuple:
    pair = _fields(obj.get(key), f"{path}.{key}", names)
    return tuple((_duration(pair, n, f"{path}.{key}", None) if as_ms else _number(pair, n, f"{path}.{key}", None))
                 for n in names)


def _rating(obj) -> RatingConfig:
    path, d = "rating", RatingConfig()
    obj = _fields(obj, path, ("alpha", "flap_lambda", "availability", "thresholds", "weights"))
    avail = _fields(obj.get("availability"), f"{path}.availability", ("floor", "full"))
    th = dict(_THRESHOLDS)
    tpath = f"{path}.thresholds"
    given = _fields(obj.get("thresholds"), tpath, ("latency", "jitter", "loss_gate_percent",
                                                    "download_mbps", "throttle", "variation"))
    for key, (good, bad) in (("latency", ("ttfb_good", "ttfb_bad")), ("jitter", ("jitter_good", "jitter_bad"))):
        g, b = _pair(given, key, tpath, ("good", "bad"), as_ms=True)
        if g is not None:
            th[good] = g * 1000
        if b is not None:
            th[bad] = b * 1000
    th["loss_gate"] = _number(given, "loss_gate_percent", tpath, th["loss_gate"], 0, 100)
    for key, names, internal in (("download_mbps", ("min", "target"), ("dl_min", "dl_target")),
                                 ("throttle", ("bad", "good"), ("throttle_bad", "throttle_good")),
                                 ("variation", ("good", "bad"), ("cv_good", "cv_bad"))):
        for name, value in zip(internal, _pair(given, key, tpath, names, as_ms=False)):
            if value is not None:
                th[name] = value
    weights = dict(_WEIGHTS)
    for name, value in _fields(obj.get("weights"), f"{path}.weights", _WEIGHTS).items():
        weights[name] = _number({name: value}, name, f"{path}.weights", 0, 0, 1)
    return RatingConfig(alpha=_number(obj, "alpha", path, d.alpha, 0, 1),
                        flap_lambda=_number(obj, "flap_lambda", path, d.flap_lambda, 0),
                        avail_floor=_number(avail, "floor", f"{path}.availability", d.avail_floor, 0, 1),
                        avail_full=_number(avail, "full", f"{path}.availability", d.avail_full, 0, 1),
                        thresholds=th, weights=weights)


@dataclass
class BalanceConfig:
    """Балансировка выбора по осям: strength 1 — поровну между значениями оси, 0 — ось
    выключена (пропорционально числу нод); недоиспользованные по трафику значения — чаще."""
    enabled: bool = False
    window: float = 86400.0
    provider: float = 1.0
    country: float = 1.0
    protocol: float = 0.3


@dataclass
class RotationConfig:
    enabled: bool = True
    interval: float = 10800.0
    jitter: float = 900.0
    # align: сроки ротации всех групп — на общей сетке слотов, один проход тестера
    # обслуживает все группы.
    align: bool = True
    min_dwell: float = 1800.0
    min_score: float = 55.0
    avoid_recent: int = 2
    balance: BalanceConfig = field(default_factory=BalanceConfig)


@dataclass
class QuarantineConfig:
    """Пауза и карантин нод, проваливших gate: после n-го провала подряд нода пропускает
    2^(n-1) проходов; дорос пропуск до after_skips — карантин на duration, затем одна
    проба. low_score > 0 — карантин и для живых, но слабых нод (не активных и не новых)."""
    after_skips: int = 32
    duration: float = 72 * 3600.0
    low_score: float = 0.0


@dataclass
class SelectionConfig:
    reserve: int = 3
    quality_margin: float = 15.0
    confirm_cycles: int = 2
    comfort_floor: float = 50.0
    cooldown: float = 300.0
    rotation: RotationConfig = field(default_factory=RotationConfig)
    quarantine: QuarantineConfig = field(default_factory=QuarantineConfig)


def _selection(obj) -> SelectionConfig:
    path, d = "selection", SelectionConfig()
    obj = _fields(obj, path, ("reserve", "quality_margin", "confirm_cycles", "comfort_floor",
                              "cooldown", "rotation", "quarantine"))
    rpath, r = f"{path}.rotation", RotationConfig()
    rot = _fields(obj.get("rotation"), rpath, ("enabled", "interval", "jitter", "align", "min_dwell",
                                               "min_score", "avoid_recent", "balance"))
    bpath, b = f"{rpath}.balance", BalanceConfig()
    bal = _fields(rot.get("balance"), bpath, ("window", "provider", "country", "protocol"))
    qpath, q = f"{path}.quarantine", QuarantineConfig()
    quar = _fields(obj.get("quarantine"), qpath, ("after_skips", "duration", "low_score"))
    return SelectionConfig(
        reserve=_number(obj, "reserve", path, d.reserve, 0, 20, integer=True),
        quality_margin=_number(obj, "quality_margin", path, d.quality_margin, 0),
        confirm_cycles=_number(obj, "confirm_cycles", path, d.confirm_cycles, 1, integer=True),
        comfort_floor=_number(obj, "comfort_floor", path, d.comfort_floor, 0, 100),
        cooldown=_duration(obj, "cooldown", path, d.cooldown),
        rotation=RotationConfig(
            enabled=_bool(rot, "enabled", rpath, r.enabled),
            interval=_duration(rot, "interval", rpath, r.interval),
            jitter=_duration(rot, "jitter", rpath, r.jitter),
            align=_bool(rot, "align", rpath, r.align),
            min_dwell=_duration(rot, "min_dwell", rpath, r.min_dwell),
            min_score=_number(rot, "min_score", rpath, r.min_score, 0, 100),
            avoid_recent=_number(rot, "avoid_recent", rpath, r.avoid_recent, 0, integer=True),
            balance=BalanceConfig(enabled="balance" in rot,
                                  window=_duration(bal, "window", bpath, b.window),
                                  provider=_number(bal, "provider", bpath, b.provider, 0, 1),
                                  country=_number(bal, "country", bpath, b.country, 0, 1),
                                  protocol=_number(bal, "protocol", bpath, b.protocol, 0, 1))),
        quarantine=QuarantineConfig(
            after_skips=_number(quar, "after_skips", qpath, q.after_skips, 1, integer=True),
            duration=_duration(quar, "duration", qpath, q.duration),
            low_score=_number(quar, "low_score", qpath, q.low_score, 0, 100)))


@dataclass
class MonitorConfig:
    """Надзор за активными нодами: идёт боевой трафик — нода жива; тихо дольше `silence` —
    внеплановый зонд (закачка probe_mb через socks); провал (нет ответа или скорость ниже
    probe_min_mbps — так ловится троттлинг) `fails` раз подряд — авария."""
    interval: float = 20.0
    reassert_drift: bool = True       # вернуть выбор селекторов, если его сменили в обход
    silence: float = 300.0
    silence_floor_mb: float = 0.0     # МБ за окно = «трафик идёт»; 0 — авто
    probe_url: str = ""               # пусто — speed.cloudflare.com
    probe_mb: float = 1.0
    probe_min_mbps: float = 1.0
    probe_timeout: float = 8.0
    fails: int = 2

    @property
    def silence_floor_bytes(self) -> int:
        """Порог «трафик идёт» в байтах за окно; 0 → авто: probe_min_mbps × silence."""
        if self.silence_floor_mb > 0:
            return int(self.silence_floor_mb * 1_000_000)
        return int(self.probe_min_mbps * 1_000_000 / 8 * self.silence)


def _monitor(obj) -> MonitorConfig:
    path, d = "monitor", MonitorConfig()
    obj = _fields(obj, path, ("interval", "reassert_drift", "silence", "silence_floor_mb", "probe", "fails"))
    ppath = f"{path}.probe"
    probe = _fields(obj.get("probe"), ppath, ("url", "size_mb", "min_mbps", "timeout"))
    return MonitorConfig(interval=_duration(obj, "interval", path, d.interval),
                         reassert_drift=_bool(obj, "reassert_drift", path, d.reassert_drift),
                         silence=_duration(obj, "silence", path, d.silence),
                         silence_floor_mb=_number(obj, "silence_floor_mb", path, d.silence_floor_mb, 0),
                         probe_url=_string(probe, "url", ppath, d.probe_url),
                         probe_mb=_number(probe, "size_mb", ppath, d.probe_mb, 0.01),
                         probe_min_mbps=_number(probe, "min_mbps", ppath, d.probe_min_mbps, 0),
                         probe_timeout=_duration(probe, "timeout", ppath, d.probe_timeout),
                         fails=_number(obj, "fails", path, d.fails, 1, integer=True))


@dataclass
class FetchConfig:
    """Загрузка подписок (nodes_fetch)."""
    every: float = 3 * 3600.0         # отдельный опрос подписок роутера; 0 — только в сборке
    timeout: float = 120.0
    retries: int = 3
    proxy: str = ""
    keep_stale: float = 48 * 3600.0   # сколько держать прошлые ноды упавшей подписки
    min_ratio: float = 0.5            # нод стало меньше этой доли — raw не перезаписывается
    happ_keys_url: str = ""           # пусто — источник по умолчанию (nodes_fetch.happ_keys)


@dataclass
class BranchConfig:
    """Ветвь сборки: router (nodes.json для sing-box роутера) или clients (whnodes.json)."""
    enabled: bool = True
    at: list[str] = field(default_factory=list)
    exclude_types: list[str] = field(default_factory=list)
    exclude_protocols: list[str] = field(default_factory=list)
    domain_resolver: str = "bootstrap"
    selector: dict = field(default_factory=dict)
    urltest: dict = field(default_factory=dict)
    rebuild: dict = field(default_factory=dict)


@dataclass
class PipelineConfig:
    timeout: float = 1800.0
    fetch: FetchConfig = field(default_factory=FetchConfig)
    router: BranchConfig = field(default_factory=lambda: BranchConfig(at=["03:00"]))
    clients: BranchConfig = field(default_factory=BranchConfig)

_REBUILD_DEFAULTS = {"degraded_for": 900.0, "few_candidates_for": 3600.0, "gone_share": 0.3,
                     "min_interval": 6 * 3600.0}
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _branch(obj, name: str) -> BranchConfig:
    path = f"pipeline.{name}"
    allowed = ["enabled", "at", "exclude_types", "exclude_protocols", "domain_resolver",
               "selector", "urltest"]
    if name == "router":
        allowed.append("rebuild")
    d = getattr(PipelineConfig(), name)
    obj = _fields(obj, path, allowed)
    at = _strings(obj, "at", path, d.at)
    for i, value in enumerate(at):
        if not _TIME.fullmatch(value):
            raise ConfigError(f"{path}.at[{i}]: {value!r} — нужно ЧЧ:ММ")
    if len(at) != len(set(at)):
        raise ConfigError(f"{path}.at: время повторяется")
    for key in ("selector", "urltest"):
        if not isinstance(obj.get(key, {}), dict):
            raise ConfigError(f"{path}.{key}: ожидается объект полей группы sing-box")
    rpath = f"{path}.rebuild"
    rebuild = _fields(obj.get("rebuild"), rpath, _REBUILD_DEFAULTS)
    return BranchConfig(
        enabled=_bool(obj, "enabled", path, True), at=at,
        exclude_types=_strings(obj, "exclude_types", path),
        exclude_protocols=[p.lower() for p in _strings(obj, "exclude_protocols", path)],
        domain_resolver=_string(obj, "domain_resolver", path, d.domain_resolver),
        selector=dict(obj.get("selector") or {}), urltest=dict(obj.get("urltest") or {}),
        rebuild={k: (_number(rebuild, k, rpath, v, 0, 1) if k == "gone_share" else _duration(rebuild, k, rpath, v))
                 for k, v in _REBUILD_DEFAULTS.items()})


def _pipeline(obj) -> PipelineConfig:
    path, d = "pipeline", PipelineConfig()
    obj = _fields(obj, path, ("timeout", "fetch", "router", "clients"))
    fpath, f = f"{path}.fetch", FetchConfig()
    fetch = _fields(obj.get("fetch"), fpath, ("every", "timeout", "retries", "proxy", "keep_stale",
                                              "min_ratio", "happ_keys_url"))
    timeout = _duration(obj, "timeout", path, d.timeout)
    if not 1 <= timeout <= 86400:
        raise ConfigError(f"{path}.timeout: от 1s до 24h")
    return PipelineConfig(
        timeout=timeout,
        fetch=FetchConfig(every=_duration(fetch, "every", fpath, f.every),
                          timeout=_duration(fetch, "timeout", fpath, f.timeout),
                          retries=_number(fetch, "retries", fpath, f.retries, 0, 10, integer=True),
                          proxy=_string(fetch, "proxy", fpath, f.proxy),
                          keep_stale=_duration(fetch, "keep_stale", fpath, f.keep_stale),
                          min_ratio=_number(fetch, "min_ratio", fpath, f.min_ratio, 0, 1),
                          happ_keys_url=_string(fetch, "happ_keys_url", fpath, f.happ_keys_url)),
        router=_branch(obj.get("router"), "router"),
        clients=_branch(obj.get("clients"), "clients"))


@dataclass
class TrafficConfig:
    enabled: bool = True
    poll: float = 5.0
    flush: float = 60.0


@dataclass
class StorageConfig:
    path: str = "results/stats.db"           # относительный — от каталога данных
    retention: float = 30 * 86400.0
    traffic: TrafficConfig = field(default_factory=TrafficConfig)

    @property
    def retention_days(self) -> float:
        return self.retention / 86400


def _storage(obj) -> StorageConfig:
    path, d = "storage", StorageConfig()
    obj = _fields(obj, path, ("path", "retention", "traffic"))
    tpath = f"{path}.traffic"
    traffic = _fields(obj.get("traffic"), tpath, ("enabled", "poll", "flush"))
    return StorageConfig(path=_string(obj, "path", path, d.path),
                         retention=_duration(obj, "retention", path, d.retention),
                         traffic=TrafficConfig(enabled=_bool(traffic, "enabled", tpath, True),
                                               poll=_duration(traffic, "poll", tpath, 5.0),
                                               flush=_duration(traffic, "flush", tpath, 60.0)))


@dataclass
class AdminConfig:
    """Веб-админка, встроенная в процесс тестера. Просмотр без токена — при public_read;
    правка и управление — только с токеном; пустой токен выключает правку. listen — адрес
    в LAN, наружу не выставлять."""
    enabled: bool = True
    listen: str = "0.0.0.0"
    port: int = 8088
    token: str = ""
    public_read: bool = True
    refresh: float = 10.0


def _admin(obj) -> AdminConfig:
    path, d = "admin", AdminConfig()
    obj = _fields(obj, path, ("enabled", "listen", "port", "token", "public_read", "refresh"))
    listen = _string(obj, "listen", path, d.listen)
    if not listen.strip():
        raise ConfigError(f"{path}.listen: нужен адрес")
    return AdminConfig(enabled=_bool(obj, "enabled", path, d.enabled), listen=listen,
                       port=_number(obj, "port", path, d.port, 1, 65535, integer=True),
                       token=_string(obj, "token", path, d.token),
                       public_read=_bool(obj, "public_read", path, d.public_read),
                       refresh=_duration(obj, "refresh", path, d.refresh))


@dataclass
class TelegramConfig:
    token: str = ""                  # токен бота (@BotFather)
    chat_id: str = ""


@dataclass
class WebhookConfig:
    url: str = ""                    # POST JSON {event, text, ts}


@dataclass
class NotifyConfig:
    """Уведомления: Telegram и/или webhook. Переключения нод — только с причинами из
    switch_reasons (ротация каждые часы — шумно)."""
    enabled: bool = True
    name: str = ""                   # подпись роутера в начале сообщения
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    proxy: str = ""                  # если Telegram без прокси недоступен
    via_tester: bool = True          # не прошло напрямую — повторить через SOCKS тестера
    events: list[str] = field(default_factory=lambda: [
        "switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox"])
    switch_reasons: list[str] = field(default_factory=lambda: ["emergency", "emergency-stuck"])
    expiry_warning: float = 3 * 86400.0
    dedupe: float = 3600.0           # не повторять одинаковое сообщение чаще
    max_per_hour: int = 20           # общий предел (защита от шквала)
    timeout: float = 10.0


def _notify(obj) -> NotifyConfig:
    from .notify import EVENTS, SWITCH_REASONS
    path, d = "notify", NotifyConfig()
    obj = _fields(obj, path, ("enabled", "name", "telegram", "webhook", "proxy", "via_tester", "events",
                              "switch_reasons", "expiry_warning", "dedupe", "max_per_hour", "timeout"))
    tg = _fields(obj.get("telegram"), f"{path}.telegram", ("token", "chat_id"))
    wh = _fields(obj.get("webhook"), f"{path}.webhook", ("url",))
    events = _strings(obj, "events", path, d.events)
    reasons = _strings(obj, "switch_reasons", path, d.switch_reasons)
    for key, values, known in (("events", events, EVENTS), ("switch_reasons", reasons, SWITCH_REASONS)):
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise ConfigError(f"{path}.{key}: неизвестное значение {unknown[0]!r} (есть: {', '.join(known)})")
    url = _string(wh, "url", f"{path}.webhook", "")
    if url and not url.startswith(("http://", "https://")):
        raise ConfigError(f"{path}.webhook.url: нужен http:// или https://")
    chat_id = tg.get("chat_id", "")
    return NotifyConfig(enabled=_bool(obj, "enabled", path, d.enabled), name=_string(obj, "name", path, ""),
                        telegram=TelegramConfig(token=_string(tg, "token", f"{path}.telegram", ""),
                                                chat_id=str(chat_id)),
                        webhook=WebhookConfig(url=url), proxy=_string(obj, "proxy", path, ""),
                        via_tester=_bool(obj, "via_tester", path, d.via_tester),
                        events=events, switch_reasons=reasons,
                        expiry_warning=_duration(obj, "expiry_warning", path, d.expiry_warning),
                        dedupe=_duration(obj, "dedupe", path, d.dedupe),
                        max_per_hour=_number(obj, "max_per_hour", path, d.max_per_hour, 1, integer=True),
                        timeout=_duration(obj, "timeout", path, d.timeout))


@dataclass
class SingboxConfig:
    """Остановка и запуск sing-box из админки, служба kill switch (только OpenWrt)."""
    init: str = "/etc/init.d/sing-box"
    killswitch_init: str = "/etc/init.d/killswitch"
    killswitch_table: str = "killswitch"     # таблица inet, которую загружает служба
    watch: float = 5.0                       # период сторожа
    down_alert: float = 60.0                 # уведомить, если sing-box лежит дольше
    stop_flag: str = "/var/run/nodes-tester/singbox-stopped"
    config: str = "/etc/sing-box/config.json"   # работающий конфиг: DNS-серверы по тегу


def _singbox(obj) -> SingboxConfig:
    path, d = "singbox", SingboxConfig()
    obj = _fields(obj, path, ("init", "config", "killswitch", "watch", "down_alert"))
    ks = _fields(obj.get("killswitch"), f"{path}.killswitch", ("init", "table"))
    return SingboxConfig(init=_string(obj, "init", path, d.init),
                         config=_string(obj, "config", path, d.config),
                         killswitch_init=_string(ks, "init", f"{path}.killswitch", d.killswitch_init),
                         killswitch_table=_string(ks, "table", f"{path}.killswitch", d.killswitch_table),
                         watch=_duration(obj, "watch", path, d.watch),
                         down_alert=_duration(obj, "down_alert", path, d.down_alert))


# --- Подписки --------------------------------------------------------------

BRANCHES = ("router", "clients", "both")
_SUB_FIELDS = ("tag", "enabled", "branch", "type", "url", "file", "path", "format", "ext",
               "user_agent", "happ_headers", "send_device", "exclude_names", "outbounds")


def _subscriptions(obj) -> list[dict]:
    if obj is None:
        return []
    if not isinstance(obj, list):
        raise ConfigError("subscriptions: ожидается список подписок")
    seen, out = set(), []
    for i, sub in enumerate(obj):
        path = f"subscriptions[{i}]"
        sub = _fields(sub, path, _SUB_FIELDS)
        tag = sub.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise ConfigError(f"{path}.tag: нужно имя провайдера")
        if tag in seen:
            raise ConfigError(f"{path}.tag: подписка {tag!r} уже есть")
        seen.add(tag)
        if sub.get("branch", "router") not in BRANCHES:
            raise ConfigError(f"{path}.branch: допустимы {', '.join(BRANCHES)}")
        kind = sub.get("type", "url")
        if kind not in ("url", "file", "folder", "inline"):
            raise ConfigError(f"{path}.type: допустимы url, file, folder, inline")
        _strings(sub, "exclude_names", path)
        if kind == "inline" and not isinstance(sub.get("outbounds"), list):
            raise ConfigError(f"{path}.outbounds: у inline-подписки нужен список outbound-ов")
        out.append(dict(sub))
    return out


def branch_subscriptions(subscriptions: list[dict], branch: str) -> list[dict]:
    """Подписки ветви сборки: router или clients (both — в обе)."""
    return [s for s in subscriptions if s.get("branch", "router") in (branch, "both")]


# --- Конфиг целиком --------------------------------------------------------

_SECTIONS = ("$schema", "api", "subscriptions", "tests", "checks", "regions", "groups", "rating",
             "selection", "tester", "monitor", "pipeline", "storage", "admin", "notify", "singbox")


@dataclass
class Config:
    api: ApiConfig = field(default_factory=ApiConfig)
    subscriptions: list[dict] = field(default_factory=list)
    tests: dict[str, dict] = field(default_factory=dict)
    checks: dict[str, dict] = field(default_factory=dict)
    regions: dict[str, list[str]] = field(default_factory=dict)
    groups: list[GroupConfig] = field(default_factory=list)
    rating: RatingConfig = field(default_factory=RatingConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    tester: TesterConfig = field(default_factory=TesterConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    singbox: SingboxConfig = field(default_factory=SingboxConfig)
    path: str = ""                   # абсолютный путь файла — для админки
    # Выход сборки роутера; whnodes.json клиентов — рядом. Каталог данных — рабочий
    # каталог процесса (у пакета — data_dir из /etc/config/nodes-tester).
    nodes_file: str = "nodes.json"

    @property
    def whnodes_file(self) -> str:
        return os.path.join(os.path.dirname(self.nodes_file), "whnodes.json")

    def raw_file(self, branch: str) -> str:
        """raw ветви (выход опроса подписок) — в raw/ рядом с nodes.json."""
        return os.path.join(os.path.dirname(self.nodes_file), "raw", f"{branch}.json")

    def group(self, tag: str) -> GroupConfig | None:
        return next((g for g in self.groups if g.tag == tag), None)


def parse_config(data: Any) -> Config:
    """dict из config.json → Config. ConfigError с путём поля при любой ошибке."""
    data = _fields(data, "", _SECTIONS)
    checks = _checks(data.get("checks"))
    regions = _regions(data.get("regions"))
    selection = _selection(data.get("selection"))
    return Config(api=_api(data.get("api")),
                  subscriptions=_subscriptions(data.get("subscriptions")),
                  tests=_tests(data.get("tests")), checks=checks, regions=regions,
                  groups=_groups(data.get("groups"), checks, regions, selection.reserve),
                  rating=_rating(data.get("rating")), selection=selection,
                  tester=_tester(data.get("tester")), monitor=_monitor(data.get("monitor")),
                  pipeline=_pipeline(data.get("pipeline")), storage=_storage(data.get("storage")),
                  admin=_admin(data.get("admin")), notify=_notify(data.get("notify")),
                  singbox=_singbox(data.get("singbox")))


def load_config(path: str, data_dir: str | None = None) -> Config:
    """Конфиг из файла. data_dir — каталог данных (nodes.json, относительный storage.path);
    по умолчанию рабочий каталог процесса (у пакета — data_dir из UCI)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Конфиг не найден: {path}. Образец — config/config.example.json.")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: некорректный JSON: {exc}") from exc
    try:
        cfg = parse_config(data)
    except ConfigError as exc:
        raise ConfigError(f"{os.path.basename(path)}: {exc}") from None
    cfg.path = os.path.abspath(path)
    data_dir = os.path.abspath(data_dir or os.getcwd())
    cfg.nodes_file = os.path.join(data_dir, cfg.nodes_file)
    cfg.storage.path = os.path.join(data_dir, cfg.storage.path)
    return cfg
