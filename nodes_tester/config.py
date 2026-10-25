"""Единый config.json: тестер, группы, проверки, подписки, конвейер, админка, уведомления.

Формат описан в docs/CONFIG.md. Как у sing-box: разделы по сущностям, сущности — массивы с
`tag`, длительности строками ("5m", "24h"), значения по умолчанию в файл не пишутся,
неизвестное поле — ошибка с путём (`groups[1].requre: неизвестное поле`).
"""

from __future__ import annotations

import json
import os
import re
import threading
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
    switch_delay: float = 1.0
    request_timeout: float = 10.0
    # Анти-ТСПУ: не чаще раза в host_gap к одному хосту с другим портом или протоколом.
    host_gap: float = 120.0


def _tester(obj) -> TesterConfig:
    path, d = "tester", TesterConfig()
    obj = _fields(obj, path, ("socks", "selector", "switch_delay", "request_timeout", "host_gap"))
    socks = _string(obj, "socks", path, d.socks)
    if not re.fullmatch(r"socks5h?://([^@/]+@)?[^:/]+:\d+", socks):
        raise ConfigError(f"{path}.socks: ожидается socks5h://хост:порт, получено {socks!r}")
    return TesterConfig(socks=socks, selector=_string(obj, "selector", path, d.selector),
                        switch_delay=_duration(obj, "switch_delay", path, d.switch_delay),
                        request_timeout=_duration(obj, "request_timeout", path, d.request_timeout),
                        host_gap=_duration(obj, "host_gap", path, d.host_gap))


# Правки config.json в процессе тестера (формы админки, расписание конвейера) — по одной:
# каждая читает, проверяет и записывает весь файл.
WRITE_LOCK = threading.Lock()

# Замеры рейтинга и их параметры. Каждый замер живёт ttl (по умолчанию 3 ч), срок
# укорачивается на случайную долю ttl_jitter (по умолчанию ttl/10).
TESTS = ("connectivity", "latency", "jitter", "download", "reachability")
_TEST_TTL = 3 * 3600.0
_TEST_FIELDS = {
    "connectivity": ("service", "timeout"),
    "latency": ("url", "timeout"),
    "jitter": ("url", "samples", "interval", "timeout"),
    "download": ("url", "duration", "window_mb", "timeout"),
    "reachability": ("urls", "timeout", "workers"),
}
_DURATION_KEYS = ("timeout", "interval", "duration", "ttl", "ttl_jitter")
_TTL_KEYS = ("ttl", "ttl_jitter")


def _with_jitter(params: dict, ttl: float) -> dict:
    """ttl и ttl_jitter параметров; ttl_jitter не задан — десятая часть ttl."""
    return {**params, "ttl": ttl, "ttl_jitter": params.get("ttl_jitter", ttl / 10)}


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
    out = {}
    for name in TESTS:
        opts = _options(obj.get(name) or {}, f"{path}.{name}", (*_TEST_FIELDS[name], *_TTL_KEYS))
        for region, over in (opts.get("for_region") or {}).items():
            _no_ttl(over, f"{path}.{name}.for_region.{region}")
        out[name] = _with_jitter(opts, opts.get("ttl", _TEST_TTL))
    for region, over in _mapping(obj.get("for_region"), f"{path}.for_region").items():
        over = _fields(over, f"{path}.for_region.{region}", TESTS)
        for name, opts in over.items():
            region_opts = _options(opts, f"{path}.for_region.{region}.{name}", _TEST_FIELDS[name])
            out[name].setdefault("for_region", {})[region] = region_opts
    return out


def _no_ttl(opts: dict, path: str) -> None:
    for key in _TTL_KEYS:
        if key in opts:
            raise ConfigError(f"{path}.{key}: срок годности задаётся только для теста целиком")


# Проверки: вердикт «да / нет / неизвестно» со сроком годности ttl.
_CHECK_DEFAULTS = {
    "country": {"ttl": 86400.0},
    "ip_type": {"ttl": 86400.0, "url": "http://ip-api.com/batch", "timeout": 10.0, "via_tester": True},
    "geo_consistent": {"ttl": 86400.0},
    "gemini": {"ttl": 86400.0, "attempts": 3, "timeout": 20.0},
    "openai": {"ttl": 86400.0, "attempts": 3, "timeout": 15.0},
    "anthropic": {"ttl": 86400.0, "attempts": 3, "timeout": 15.0},
    "heavy": {"ttl": 21600.0, "url": "https://speed.cloudflare.com/__down?bytes=20000000",
              "duration": 30.0, "window_mb": 2, "min_mbps": 5.0},
    "upstream": {"ttl": 21600.0, "outbound": "", "url": "https://www.cloudflare.com/cdn-cgi/trace",
                 "attempts": 2, "timeout": 15.0},
    "whitelist": {"ttl": 86400.0, "ip_list": "https://github.com/ancored/ru-mobile-whitelist/releases/latest/download/ru-mobile-whitelist-ip.txt",
                  "sni_list": "https://github.com/ancored/ru-mobile-whitelist/releases/latest/download/ru-mobile-whitelist-sni.txt", "dns": "dns-ru", "timeout": 10.0},
}
CHECKS = tuple(_CHECK_DEFAULTS)
# Параметры проверки внутри группы (require/prefer): условия отбора. У ip_type и
# geo_consistent своего срока нет: сведения ip-api обновляются по checks.ip_type.ttl.
_GROUP_CHECK_FIELDS = {
    "country": (*_TTL_KEYS, "countries", "regions", "exclude_countries", "exclude_regions"),
    "ip_type": ("kinds",),
    "geo_consistent": (),
}
IP_KINDS = ("residential", "mobile", "hosting", "proxy")


def _checks(obj) -> dict[str, dict]:
    path = "checks"
    obj = _fields(obj, path, CHECKS)
    out = {}
    for name, defaults in _CHECK_DEFAULTS.items():
        given = _options(obj.get(name) or {}, f"{path}.{name}", (*defaults, "ttl_jitter"))
        merged = {**defaults, **given}
        out[name] = _with_jitter(merged, merged["ttl"])
    return out


@dataclass
class GroupConfig:
    """Группа sing-box `{tag}-auto-out`: в ней все ноды, активную выбирает тестер по проверкам."""
    tag: str
    strict: bool = False            # без кандидата — block, а не failsafe-urltest
    direct_fallback: bool = False   # в крайнем случае — direct-out
    in_global: bool = True          # входит в global-auto-out
    reserve: int = 3
    tier_floor: float | None = None  # None — selection.tier_floor
    require: dict[str, dict] = field(default_factory=dict)   # проверка → условия
    prefer: dict[str, dict] = field(default_factory=dict)
    errors: "ErrorRuleConfig | None" = None   # None — monitor.errors
    exclude_subscriptions: list[str] = field(default_factory=list)   # их ноды не в группе


_TAG = re.compile(r"[a-z0-9][a-z0-9_]*")


def _group_checks(obj, path: str, checks: dict) -> dict[str, dict]:
    obj = _fields(obj, path, CHECKS)
    out = {}
    for name, params in obj.items():
        if name == "upstream" and not checks["upstream"]["outbound"]:
            raise ConfigError(f"{path}.upstream: не задан checks.upstream.outbound")
        allowed = _GROUP_CHECK_FIELDS.get(name, _TTL_KEYS)
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
        if "ttl" in params:
            out[name] = _with_jitter(params, params["ttl"])
        elif "ttl" in allowed:
            out[name] = {"ttl": checks[name]["ttl"], "ttl_jitter": checks[name]["ttl_jitter"], **params}
        else:
            out[name] = params
    return out


def _group_tag(item: dict, path: str, seen: set) -> str:
    """Тег группы (уникальный в своём списке); добавляется в seen."""
    tag = item.get("tag")
    if not isinstance(tag, str) or not _TAG.fullmatch(tag) or tag in ("global", "nodes", "failsafe"):
        raise ConfigError(f"{path}.tag: латиница в нижнем регистре, цифры и _; не global, nodes, "
                          f"failsafe (получено {tag!r})")
    if tag in seen:
        raise ConfigError(f"{path}.tag: группа {tag!r} уже есть")
    seen.add(tag)
    return tag


def _groups(obj, checks: dict, regions: dict, reserve: int, errors: "ErrorRuleConfig") -> list[GroupConfig]:
    if obj is None:
        obj = [{"tag": "all"}]
    if not isinstance(obj, list):
        raise ConfigError("groups: ожидается список групп")
    out, seen = [], set()
    for i, item in enumerate(obj):
        path = f"groups[{i}]"
        item = _fields(item, path, ("tag", "strict", "direct_fallback", "in_global", "reserve",
                                    "tier_floor", "require", "prefer", "monitor", "exclude_subscriptions"))
        tag = _group_tag(item, path, seen)
        group = GroupConfig(tag=tag, strict=_bool(item, "strict", path, False),
                            direct_fallback=_bool(item, "direct_fallback", path, False),
                            in_global=_bool(item, "in_global", path, True),
                            reserve=_number(item, "reserve", path, reserve, 0, 20, integer=True),
                            tier_floor=_number(item, "tier_floor", path, None, 0, 100),
                            require=_group_checks(item.get("require"), f"{path}.require", checks),
                            prefer=_group_checks(item.get("prefer"), f"{path}.prefer", checks),
                            exclude_subscriptions=_strings(item, "exclude_subscriptions", path))
        own = _fields(item.get("monitor"), f"{path}.monitor", ("errors",))
        if "errors" in own:
            group.errors = _error_rule(own["errors"], f"{path}.monitor.errors", errors)
        for part in ("require", "prefer"):
            for key in ("regions", "exclude_regions"):
                unknown = [r for r in getattr(group, part).get("country", {}).get(key, [])
                           if r not in regions]
                if unknown:
                    raise ConfigError(f"{path}.{part}.country.{key}: неизвестный регион {unknown[0]!r} "
                                      f"(есть: {', '.join(sorted(regions))})")
        out.append(group)
    return out


# Проверки, которые тестер ведёт для каждой ноды (замеры, ip-api, белые списки): по ним можно
# отбирать ноды клиентских групп. Проверки доступа к сервисам и heavy гоняет только резерв
# групп роутера.
CLIENT_CHECKS = ("country", "ip_type", "geo_consistent", "whitelist")


def _client_groups(obj, checks: dict, regions: dict) -> list[GroupConfig]:
    """pipeline.clients.groups: urltest `{tag}-auto-out` клиентов из нод, прошедших обязательные
    проверки на роутере; группы с in_global — в global-auto-out (на неё ссылаются базы клиентов)."""
    path = "pipeline.clients.groups"
    if obj is None:
        obj = [{"tag": "all"}]
    if not isinstance(obj, list):
        raise ConfigError(f"{path}: ожидается список групп")
    out, seen = [], set()
    for i, item in enumerate(obj):
        ipath = f"{path}[{i}]"
        item = _fields(item, ipath, ("tag", "in_global", "require", "exclude_subscriptions"))
        tag = _group_tag(item, ipath, seen)
        require = _fields(item.get("require"), f"{ipath}.require", CLIENT_CHECKS)
        group = GroupConfig(tag=tag, in_global=_bool(item, "in_global", ipath, True),
                            require=_group_checks(require, f"{ipath}.require", checks),
                            exclude_subscriptions=_strings(item, "exclude_subscriptions", ipath))
        unknown = [r for key in ("regions", "exclude_regions")
                   for r in group.require.get("country", {}).get(key, []) if r not in regions]
        if unknown:
            raise ConfigError(f"{ipath}.require.country: неизвестный регион {unknown[0]!r} "
                              f"(есть: {', '.join(sorted(regions))})")
        out.append(group)
    if out and not any(g.in_global for g in out):
        raise ConfigError(f"{path}: нужна хотя бы одна группа с in_global — базы клиентов ссылаются "
                          f"на global-auto-out")
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
    # 6 ч: чаще всего нода отказывает в первые минуты после выбора, а не от долгой работы —
    # каждая ротация лишний раз рискует новой нодой.
    interval: float = 6 * 3600.0
    jitter: float = 900.0
    min_dwell: float = 1800.0
    # Плановые ротации разных групп не ближе этого: каждая рвёт соединения своей группы.
    spread: float = 600.0
    min_score: float = 55.0
    avoid_recent: int = 2
    balance: BalanceConfig = field(default_factory=BalanceConfig)


@dataclass
class QuarantineConfig:
    """Пауза и карантин нод, проваливших gate: после n-го провала подряд нода не проверяется
    2^(n-1) сроков connectivity; дорос пропуск до after_skips — карантин на duration, затем одна
    проба. low_score > 0 — карантин и для живых, но слабых нод (не активных и не новых)."""
    after_skips: int = 32
    duration: float = 72 * 3600.0
    low_score: float = 0.0


@dataclass
class StabilityConfig:
    """Надёжность в боевой работе: рейтинг умножается на 1 / (1 + weight × частота аварий в час).
    Частота — аварии ноды за window на часы её работы активной, сглаженные к частоте её
    поставщика (prior_hours часов «как у поставщика»). Поставщик с работой меньше
    provider_min_hours — к средней частоте всех. Множитель не ниже min."""
    enabled: bool = True
    window: float = 7 * 86400.0
    weight: float = 1.0
    prior_hours: float = 2.0
    provider_min_hours: float = 5.0
    min: float = 0.3


@dataclass
class SelectionConfig:
    reserve: int = 3
    quality_margin: float = 15.0
    reserve_margin: float = 15.0              # не задан — как quality_margin
    confirm_cycles: int = 2
    comfort_floor: float = 50.0
    tier_floor: float = 75.0                  # рейтинг ниже — ярус после всех остальных
    cooldown: float = 300.0
    host_penalty: float = 10800.0             # хост ноды, снятой аварией монитора, не выбирать
    rotation: RotationConfig = field(default_factory=RotationConfig)
    quarantine: QuarantineConfig = field(default_factory=QuarantineConfig)
    stability: StabilityConfig = field(default_factory=StabilityConfig)


def _stability(obj, path: str) -> StabilityConfig:
    d = StabilityConfig()
    obj = _fields(obj, path, ("enabled", "window", "weight", "prior_hours", "provider_min_hours", "min"))
    return StabilityConfig(enabled=_bool(obj, "enabled", path, d.enabled),
                           window=_duration(obj, "window", path, d.window),
                           weight=_number(obj, "weight", path, d.weight, 0),
                           prior_hours=_number(obj, "prior_hours", path, d.prior_hours, 0),
                           provider_min_hours=_number(obj, "provider_min_hours", path, d.provider_min_hours, 0),
                           min=_number(obj, "min", path, d.min, 0, 1))


def _selection(obj) -> SelectionConfig:
    path, d = "selection", SelectionConfig()
    obj = _fields(obj, path, ("reserve", "quality_margin", "reserve_margin", "confirm_cycles", "comfort_floor",
                              "tier_floor", "cooldown", "host_penalty", "rotation", "quarantine", "stability"))
    rpath, r = f"{path}.rotation", RotationConfig()
    rot = _fields(obj.get("rotation"), rpath, ("enabled", "interval", "jitter", "min_dwell", "spread",
                                               "min_score", "avoid_recent", "balance"))
    bpath, b = f"{rpath}.balance", BalanceConfig()
    bal = _fields(rot.get("balance"), bpath, ("window", "provider", "country", "protocol"))
    qpath, q = f"{path}.quarantine", QuarantineConfig()
    quar = _fields(obj.get("quarantine"), qpath, ("after_skips", "duration", "low_score"))
    quality_margin = _number(obj, "quality_margin", path, d.quality_margin, 0)
    return SelectionConfig(
        reserve=_number(obj, "reserve", path, d.reserve, 0, 20, integer=True),
        quality_margin=quality_margin,
        reserve_margin=_number(obj, "reserve_margin", path, quality_margin, 0),
        confirm_cycles=_number(obj, "confirm_cycles", path, d.confirm_cycles, 1, integer=True),
        comfort_floor=_number(obj, "comfort_floor", path, d.comfort_floor, 0, 100),
        tier_floor=_number(obj, "tier_floor", path, d.tier_floor, 0, 100),
        cooldown=_duration(obj, "cooldown", path, d.cooldown),
        host_penalty=_duration(obj, "host_penalty", path, d.host_penalty),
        rotation=RotationConfig(
            enabled=_bool(rot, "enabled", rpath, r.enabled),
            interval=_duration(rot, "interval", rpath, r.interval),
            jitter=_duration(rot, "jitter", rpath, r.jitter),
            min_dwell=_duration(rot, "min_dwell", rpath, r.min_dwell),
            spread=_duration(rot, "spread", rpath, r.spread),
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
            low_score=_number(quar, "low_score", qpath, q.low_score, 0, 100)),
        stability=_stability(obj.get("stability"), f"{path}.stability"))


ERROR_CLASSES = ("connect", "timeout", "reset", "canceled")


@dataclass
class ErrorRuleConfig:
    """Авария по ошибкам соединений через группу из лога sing-box: за последние `window`
    не меньше `min` ошибок классов `classes` и не меньше `prev_min` за окно до него (ошибки
    держатся дольше окна; 0 — без этого условия). Первые `fresh` после выбора ноды хватает
    `fresh_min` ошибок за окно без условия о предыдущем: чаще всего нода отказывает сразу после
    выбора. Ошибки соединений, начатых до смены ноды, не считаются (время начала — по возрасту
    соединения в строке лога). probe_confirm — перед аварией один зонд, прошёл — не менять."""
    enabled: bool = True
    window: float = 60.0
    min: int = 3
    prev_min: int = 2
    classes: tuple = ("connect", "timeout", "reset")
    fresh: float = 600.0
    fresh_min: int = 2
    probe_confirm: bool = False


@dataclass
class OutageConfig:
    """Общая авария — отказ не ноды, а общего звена (WAN, DNS нод): все проверки и замены
    останавливаются, пока прямой выход и ноды не ответят `recover` проверок подряд.
    Прямой выход проверяется раз в `interval` (0 — только по сигналу) рукопожатием TCP+TLS с
    `targets` мимо sing-box; не ответили `fail_quorum` целей — авария WAN. Сигнал — `switches`
    аварийных замен за `switches_window`: проверяется прямой выход и до `nodes` нод разных
    поставщиков; прямой жив, а ноды все молчат — авария нод."""
    enabled: bool = True
    interval: float = 60.0
    targets: tuple = ("77.88.8.8:443", "vk.com:443", "www.gosuslugi.ru:443")
    fail_quorum: int = 2
    timeout: float = 3.0
    switches: int = 3
    switches_window: float = 600.0
    nodes: int = 3
    recheck: float = 30.0
    recover: int = 3
    mark: int = 0x2024             # routing_mark sing-box: такой трафик он не перехватывает
    interface: str = ""            # пусто — route.default_interface из конфига sing-box


@dataclass
class MonitorConfig:
    """Надзор за активными нодами: идёт боевой трафик — нода жива; тихо дольше `silence` и
    были соединения — внеплановый зонд (закачка probe_mb через socks); провал (нет ответа или
    скорость ниже probe_min_mbps — так ловится троттлинг) `fails` раз подряд — авария. Ошибки
    соединений в логе sing-box — авария по правилу `errors`."""
    interval: float = 10.0
    reassert_drift: bool = True       # вернуть выбор селекторов, если его сменили в обход
    silence: float = 300.0
    silence_floor_mb: float = 0.0     # МБ за окно = «трафик идёт»; 0 — авто
    probe_url: str = ""               # пусто — speed.cloudflare.com
    probe_mb: float = 1.0
    probe_min_mbps: float = 1.0
    probe_timeout: float = 8.0
    fails: int = 2
    retry: float = 30.0               # повтор зонда после провала
    error_burst: int = 5              # ошибок соединений через группу за тик — зонд; 0 — выкл.
    silence_min_conns: int = 1        # меньше соединений за тишину — группой не пользуются, зонд не нужен
    errors: ErrorRuleConfig = field(default_factory=ErrorRuleConfig)
    outage: OutageConfig = field(default_factory=OutageConfig)

    @property
    def silence_floor_bytes(self) -> int:
        """Порог «трафик идёт» в байтах за окно; 0 → авто: probe_min_mbps × silence."""
        if self.silence_floor_mb > 0:
            return int(self.silence_floor_mb * 1_000_000)
        return int(self.probe_min_mbps * 1_000_000 / 8 * self.silence)


def _error_rule(obj, path: str, base: ErrorRuleConfig) -> ErrorRuleConfig:
    """Правило аварии по ошибкам; незаданные поля — из base (monitor.errors для группы)."""
    obj = _fields(obj, path, ("enabled", "window", "min", "prev_min", "classes", "fresh", "fresh_min",
                              "probe_confirm"))
    classes = tuple(_strings(obj, "classes", path, base.classes))
    unknown = [c for c in classes if c not in ERROR_CLASSES]
    if unknown or not classes:
        raise ConfigError(f"{path}.classes: допустимы {', '.join(ERROR_CLASSES)} (получено {list(classes)})")
    return ErrorRuleConfig(enabled=_bool(obj, "enabled", path, base.enabled),
                           window=_duration(obj, "window", path, base.window),
                           min=_number(obj, "min", path, base.min, 1, integer=True),
                           prev_min=_number(obj, "prev_min", path, base.prev_min, 0, integer=True),
                           classes=classes,
                           fresh=_duration(obj, "fresh", path, base.fresh),
                           fresh_min=_number(obj, "fresh_min", path, base.fresh_min, 1, integer=True),
                           probe_confirm=_bool(obj, "probe_confirm", path, base.probe_confirm))


def _outage(obj, path: str) -> OutageConfig:
    d = OutageConfig()
    obj = _fields(obj, path, ("enabled", "interval", "targets", "fail_quorum", "timeout", "switches",
                              "switches_window", "nodes", "recheck", "recover", "mark", "interface"))
    targets = tuple(_strings(obj, "targets", path, d.targets))
    bad = [t for t in targets if not re.fullmatch(r"[A-Za-z0-9.-]+:\d+", t)]
    if bad:
        raise ConfigError(f"{path}.targets: {bad[0]!r} — нужно хост:порт")
    quorum = _number(obj, "fail_quorum", path, d.fail_quorum, 1, integer=True)
    if targets and quorum > len(targets):
        raise ConfigError(f"{path}.fail_quorum: {quorum} больше числа целей ({len(targets)})")
    return OutageConfig(enabled=_bool(obj, "enabled", path, d.enabled),
                        interval=_duration(obj, "interval", path, d.interval),
                        targets=targets, fail_quorum=quorum,
                        timeout=_duration(obj, "timeout", path, d.timeout),
                        switches=_number(obj, "switches", path, d.switches, 1, integer=True),
                        switches_window=_duration(obj, "switches_window", path, d.switches_window),
                        nodes=_number(obj, "nodes", path, d.nodes, 0, integer=True),
                        recheck=_duration(obj, "recheck", path, d.recheck),
                        recover=_number(obj, "recover", path, d.recover, 1, integer=True),
                        mark=_number(obj, "mark", path, d.mark, 0, integer=True),
                        interface=_string(obj, "interface", path, d.interface))


def _monitor(obj) -> MonitorConfig:
    path, d = "monitor", MonitorConfig()
    obj = _fields(obj, path, ("interval", "reassert_drift", "silence", "silence_floor_mb", "probe", "fails",
                              "retry", "error_burst", "silence_min_conns", "errors", "outage"))
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
                         fails=_number(obj, "fails", path, d.fails, 1, integer=True),
                         retry=_duration(obj, "retry", path, d.retry),
                         error_burst=_number(obj, "error_burst", path, d.error_burst, 0, integer=True),
                         silence_min_conns=_number(obj, "silence_min_conns", path, d.silence_min_conns, 0,
                                                   integer=True),
                         errors=_error_rule(obj.get("errors"), f"{path}.errors", d.errors),
                         outage=_outage(obj.get("outage"), f"{path}.outage"))


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
    """Ветвь сборки: router (nodes.json для sing-box роутера) или clients (whnodes.json из
    проверенных на роутере нод)."""
    enabled: bool = True
    at: list[str] = field(default_factory=list)
    exclude_types: list[str] = field(default_factory=list)
    exclude_protocols: list[str] = field(default_factory=list)
    domain_resolver: str = "bootstrap"
    selector: dict = field(default_factory=dict)
    urltest: dict = field(default_factory=dict)
    rebuild: dict = field(default_factory=dict)
    groups: list[GroupConfig] = field(default_factory=list)   # только clients
    output: str = "/etc/sing-box-clients"     # clients: куда публикуются конфиги
    url: str = ""                             # clients: адрес раздачи (ссылка = url + токен)


@dataclass
class PipelineConfig:
    timeout: float = 1800.0
    fetch: FetchConfig = field(default_factory=FetchConfig)
    router: BranchConfig = field(default_factory=lambda: BranchConfig(at=["03:00"]))
    clients: BranchConfig = field(default_factory=BranchConfig)

_REBUILD_DEFAULTS = {"degraded_for": 900.0, "few_candidates_for": 3600.0, "gone_share": 0.3,
                     "min_interval": 6 * 3600.0}
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _branch(obj, name: str, checks: dict, regions: dict) -> BranchConfig:
    path = f"pipeline.{name}"
    allowed = ["enabled", "at", "exclude_types", "exclude_protocols", "domain_resolver",
               "selector", "urltest"]
    allowed += ["rebuild"] if name == "router" else ["groups", "output", "url"]
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
    url = _string(obj, "url", path, d.url)
    if url and not re.fullmatch(r"https?://[^/\s]+/(\S*/)?", url):
        raise ConfigError(f"{path}.url: нужен адрес вида https://хост:порт/ (с / в конце), получено {url!r}")
    return BranchConfig(
        enabled=_bool(obj, "enabled", path, True), at=at,
        exclude_types=_strings(obj, "exclude_types", path),
        exclude_protocols=[p.lower() for p in _strings(obj, "exclude_protocols", path)],
        domain_resolver=_string(obj, "domain_resolver", path, d.domain_resolver),
        selector=dict(obj.get("selector") or {}), urltest=dict(obj.get("urltest") or {}),
        rebuild={k: (_number(rebuild, k, rpath, v, 0, 1) if k == "gone_share" else _duration(rebuild, k, rpath, v))
                 for k, v in _REBUILD_DEFAULTS.items()},
        groups=_client_groups(obj.get("groups"), checks, regions) if name == "clients" else [],
        output=_string(obj, "output", path, d.output) or d.output, url=url)


def _pipeline(obj, checks: dict, regions: dict) -> PipelineConfig:
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
        router=_branch(obj.get("router"), "router", checks, regions),
        clients=_branch(obj.get("clients"), "clients", checks, regions))


@dataclass
class TrafficConfig:
    enabled: bool = True
    poll: float = 5.0
    flush: float = 60.0


@dataclass
class VacuumConfig:
    """VACUUM базы тестера: не реже раза в every, в ближайшее время at (ЧЧ:ММ, местное).
    every 0 — только вручную из админки."""
    every: float = 30 * 86400.0
    at: str = "04:00"


@dataclass
class StorageConfig:
    path: str = "results/stats.db"           # относительный — от каталога данных
    retention: float = 30 * 86400.0
    traffic: TrafficConfig = field(default_factory=TrafficConfig)
    vacuum: VacuumConfig = field(default_factory=VacuumConfig)

    @property
    def retention_days(self) -> float:
        return self.retention / 86400


def _storage(obj) -> StorageConfig:
    path, d = "storage", StorageConfig()
    obj = _fields(obj, path, ("path", "retention", "traffic", "vacuum"))
    tpath, vpath = f"{path}.traffic", f"{path}.vacuum"
    traffic = _fields(obj.get("traffic"), tpath, ("enabled", "poll", "flush"))
    vacuum = _fields(obj.get("vacuum"), vpath, ("every", "at"))
    at = _string(vacuum, "at", vpath, d.vacuum.at)
    if not _TIME.fullmatch(at):
        raise ConfigError(f"{vpath}.at: {at!r} — нужно ЧЧ:ММ")
    return StorageConfig(path=_string(obj, "path", path, d.path),
                         retention=_duration(obj, "retention", path, d.retention),
                         traffic=TrafficConfig(enabled=_bool(traffic, "enabled", tpath, True),
                                               poll=_duration(traffic, "poll", tpath, 5.0),
                                               flush=_duration(traffic, "flush", tpath, 60.0)),
                         vacuum=VacuumConfig(every=_duration(vacuum, "every", vpath, d.vacuum.every), at=at))


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
        "switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox", "outage"])
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
    log_max: float = 5.0                     # МБ: лог sing-box больше — в .1 и очистить; 0 — нет


def _singbox(obj) -> SingboxConfig:
    path, d = "singbox", SingboxConfig()
    obj = _fields(obj, path, ("init", "config", "killswitch", "watch", "down_alert", "log_max"))
    ks = _fields(obj.get("killswitch"), f"{path}.killswitch", ("init", "table"))
    return SingboxConfig(init=_string(obj, "init", path, d.init),
                         config=_string(obj, "config", path, d.config),
                         killswitch_init=_string(ks, "init", f"{path}.killswitch", d.killswitch_init),
                         killswitch_table=_string(ks, "table", f"{path}.killswitch", d.killswitch_table),
                         watch=_duration(obj, "watch", path, d.watch),
                         down_alert=_duration(obj, "down_alert", path, d.down_alert),
                         log_max=_number(obj, "log_max", path, d.log_max, 0))


# --- Подписки --------------------------------------------------------------

BRANCHES = ("router", "clients", "both")
_SUB_FIELDS = ("tag", "enabled", "branch", "type", "url", "file", "path", "format", "ext",
               "user_agent", "happ_headers", "send_device", "exclude_names", "outbounds",
               "gateway_key", "slots", "min_rating", "exclude_countries", "proxy", "gateways", "relay")


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
        if kind not in ("url", "file", "folder", "inline", "amnezia"):
            raise ConfigError(f"{path}.type: допустимы url, file, folder, inline, amnezia")
        _strings(sub, "exclude_names", path)
        if kind == "inline" and not isinstance(sub.get("outbounds"), list):
            raise ConfigError(f"{path}.outbounds: у inline-подписки нужен список outbound-ов")
        if kind == "amnezia":
            if not str(sub.get("url", "")).startswith("vpn://"):
                raise ConfigError(f"{path}.url: у amnezia-подписки нужна ссылка vpn://")
            for key in ("path", "gateway_key"):
                if not _string(sub, key, path, "").strip():
                    raise ConfigError(f"{path}.{key}: у amnezia-подписки обязательно")
            _number(sub, "slots", path, None, 1, integer=True)
            _number(sub, "min_rating", path, None, 0, 100)
            _strings(sub, "exclude_countries", path)
            _string(sub, "proxy", path, "")
            for url in _strings(sub, "gateways", path):
                if not url.startswith(("http://", "https://")):
                    raise ConfigError(f"{path}.gateways: {url!r} — нужен адрес http:// или https://")
            _bool(sub, "relay", path, True)
        elif "proxy" in sub:
            raise ConfigError(f"{path}.proxy: только у amnezia-подписки (остальные качаются через pipeline.fetch.proxy)")
        out.append(dict(sub))
    return out


def branch_subscriptions(subscriptions: list[dict], branch: str) -> list[str]:
    """Теги подписок, чьи ноды идут в группы ветви: router или clients (both — в обе). Опрашиваются
    и тестируются на роутере все подписки: ноды подписок только для клиентов в sing-box роутера
    есть лишь в селекторе тестера."""
    return [s["tag"] for s in subscriptions if s.get("branch", "router") in (branch, "both")]


# --- Клиенты ---------------------------------------------------------------

CLIENT_NAME = re.compile(r"[A-Za-z0-9_-]+")
CLIENT_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,}")


def _clients(obj) -> list[dict]:
    """Клиенты: имя (база singbox/clients/base_<имя>.json или готовый файл clients/publish/<имя>.json)
    и секретный токен ссылки; enabled: false — конфиг не собирается, ссылка не работает."""
    if obj is None:
        return []
    if not isinstance(obj, list):
        raise ConfigError("clients: ожидается список клиентов")
    out, names, tokens = [], set(), set()
    for i, item in enumerate(obj):
        path = f"clients[{i}]"
        item = _fields(item, path, ("name", "token", "enabled"))
        name, token = item.get("name"), item.get("token")
        if not isinstance(name, str) or not CLIENT_NAME.fullmatch(name):
            raise ConfigError(f"{path}.name: латиница, цифры, _ и - (получено {name!r})")
        if not isinstance(token, str) or not CLIENT_TOKEN.fullmatch(token):
            raise ConfigError(f"{path}.token: не короче 16 символов из латиницы, цифр, _ и -")
        if name in names:
            raise ConfigError(f"{path}.name: клиент {name!r} уже есть")
        if token in tokens:
            raise ConfigError(f"{path}.token: такой токен уже у другого клиента")
        names.add(name)
        tokens.add(token)
        out.append({"name": name, "token": token, "enabled": _bool(item, "enabled", path, True)})
    return out


# --- Конфиг целиком --------------------------------------------------------

_SECTIONS = ("$schema", "api", "subscriptions", "clients", "tests", "checks", "regions", "groups",
             "rating", "selection", "tester", "monitor", "pipeline", "storage", "admin", "notify",
             "singbox")


@dataclass
class Config:
    api: ApiConfig = field(default_factory=ApiConfig)
    subscriptions: list[dict] = field(default_factory=list)
    clients: list[dict] = field(default_factory=list)
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

    @property
    def client_candidates_file(self) -> str:
        """Кандидаты клиентских групп по проверкам тестера — вход сборки клиентов."""
        return os.path.join(os.path.dirname(self.nodes_file), "client-candidates.json")

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
    monitor = _monitor(data.get("monitor"))
    subscriptions = _subscriptions(data.get("subscriptions"))
    groups = _groups(data.get("groups"), checks, regions, selection.reserve, monitor.errors)
    pipeline = _pipeline(data.get("pipeline"), checks, regions)
    for path, branch, items in (("groups", "router", groups),
                                ("pipeline.clients.groups", "clients", pipeline.clients.groups)):
        own = branch_subscriptions(subscriptions, branch)
        for i, group in enumerate(items):
            unknown = [t for t in group.exclude_subscriptions if t not in own]
            if unknown:
                raise ConfigError(f"{path}[{i}].exclude_subscriptions: нет подписки ветви {branch} "
                                  f"{unknown[0]!r}")
    return Config(api=_api(data.get("api")),
                  subscriptions=subscriptions, clients=_clients(data.get("clients")),
                  tests=_tests(data.get("tests")), checks=checks, regions=regions,
                  groups=groups,
                  rating=_rating(data.get("rating")), selection=selection,
                  tester=_tester(data.get("tester")), monitor=monitor,
                  pipeline=pipeline, storage=_storage(data.get("storage")),
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
