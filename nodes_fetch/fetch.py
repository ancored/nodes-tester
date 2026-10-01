"""Стадия fetch: providers.json → raw_nodes.json (контракт — nodes_common.raw).

fetch — «честное зеркало» подписок: ничего не фильтрует и не переименовывает (это
nodes_config). Его задачи — надёжно достать ноды:
- каждая подписка изолирована: сбой одной не валит остальные;
- упавшая подписка (ошибка/пусто) берёт свои ноды из прошлого raw, пока они моложе
  stale_max_hours (last-good), с пометкой stale;
- guard min_ratio: если нод стало меньше доли от прошлого — не перезаписываем файл;
- --only: обновить часть подписок, остальные переносятся из прошлого raw как есть.
"""

import os
from datetime import datetime, timedelta, timezone

from nodes_common import raw as rawfmt
from nodes_common.fileio import parse_iso

from . import meta as metamod
from . import sources, util

FETCH_DEFAULTS = {
    "timeout": 120,           # чтение, сек (connect — 30)
    "retries": 3,             # повторы HTTP при отсутствии ответа
    "proxy": None,            # "socks5://127.0.0.1:2080" — качать подписки через туннель
    "stale_max_hours": 48,    # сколько держать last-good ноды упавшей подписки
    "min_ratio": 0.5,         # guard: нод < min_ratio × прошлое → не записывать
}

# Ключи v1 (общий providers.json старого subscribe), которые относятся к nodes_config.
CONFIG_STAGE_KEYS = ("save_config_path", "exclude_protocol", "exclude_countries",
                     "exclude_node_protocols", "labels", "domain_resolver_tag", "auto_backup")
_LEGACY_SUB_KEYS = ("prefix", "emoji", "subgroup")


class ProvidersError(ValueError):
    """Некорректный providers.json."""


class GuardError(RuntimeError):
    """Сработал guard min_ratio — raw не перезаписан."""


def load_providers(data, log=print):
    """dict providers (v2 или совместимый v1) → нормализованный вид.
    {subscribes: [{tag, kind, enabled, url|file|path…, user_agent, happ_headers}], fetch: {…}}"""
    if not isinstance(data, dict) or not isinstance(data.get("subscribes"), list):
        raise ProvidersError("providers: нужен объект с массивом 'subscribes'")
    subs, seen = [], set()
    for i, sub in enumerate(data["subscribes"]):
        if not isinstance(sub, dict):
            raise ProvidersError(f"subscribes[{i}]: ожидается объект")
        tag = sub.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise ProvidersError(f"subscribes[{i}]: нет 'tag'")
        if tag in seen:
            raise ProvidersError(f"subscribes: повторяющийся tag {tag!r}")
        seen.add(tag)
        kind = sources.kind_of(sub)
        if kind is None:
            raise ProvidersError(f"подписка {tag!r}: нужен 'url', 'file' или type='folder'")
        for key in _LEGACY_SUB_KEYS:
            if sub.get(key):
                log(f"  [fetch] подписка {tag!r}: ключ {key!r} устарел и игнорируется")
        norm = dict(sub)
        norm["kind"] = kind
        norm["enabled"] = sub.get("enabled", True) is not False
        for key in ("user_agent", "User-Agent"):
            if key in sub and not isinstance(sub[key], str):
                raise ProvidersError(f"подписка {tag!r}: {key} должен быть строкой")
        headers = sub.get("happ_headers", {})
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ProvidersError(f"подписка {tag!r}: happ_headers должен быть объектом со строковыми значениями")
        if any(not k.strip() or any(c in k + v for c in '\r\n') for k, v in headers.items()):
            raise ProvidersError(f"подписка {tag!r}: некорректное имя или значение HTTP-заголовка")
        if "send_device" in sub and not isinstance(sub["send_device"], bool):
            raise ProvidersError(f"подписка {tag!r}: send_device должен быть true/false")
        if "User-Agent" in norm and "user_agent" not in norm:
            norm["user_agent"] = norm.pop("User-Agent")
        subs.append(norm)
    fetch_cfg = dict(FETCH_DEFAULTS)
    fetch_cfg.update({k: v for k, v in (data.get("fetch") or {}).items() if k in FETCH_DEFAULTS})
    for key in ("timeout", "retries", "stale_max_hours", "min_ratio"):   # как в providers.schema.json
        v = fetch_cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 \
                or (key == "timeout" and v == 0) or (key == "retries" and not isinstance(v, int)):
            raise ProvidersError(f"fetch.{key}: недопустимое значение {v!r}")
    if not (0 <= float(fetch_cfg["min_ratio"]) <= 1):
        raise ProvidersError("fetch.min_ratio должен быть в [0, 1]")
    return {"subscribes": subs, "fetch": fetch_cfg}


def _index_previous(previous):
    """provider → (nodes, source) из прошлого raw."""
    nodes, srcs = {}, {}
    if previous:
        for node in previous.get("nodes", []):
            nodes.setdefault(node["provider"], []).append(node)
        for src in previous.get("sources", []):
            srcs[src.get("provider")] = src
    return nodes, srcs


def run(providers, base_dir, name="providers", previous=None, only=None, now=None, log=print,
        device=None):
    """Прогон fetch → raw-конверт (dict). providers — результат load_providers.
    previous — прошлый raw (для last-good/--only); now — aware datetime (для тестов);
    device — device.json установки (HWID для панелей)."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    cfg = providers["fetch"]
    ctx = sources.Context(base_dir=base_dir,
                          timeout=(util.DEFAULT_TIMEOUT[0], float(cfg["timeout"])),
                          retries=int(cfg["retries"]), proxy=cfg["proxy"], log=log,
                          device=device)
    stale_max = timedelta(hours=float(cfg["stale_max_hours"]))
    prev_nodes, prev_srcs = _index_previous(previous)
    only = set(only or ())

    out_nodes, out_sources = [], []
    for sub in providers["subscribes"]:
        tag = sub["tag"]
        if not sub["enabled"]:
            continue
        if only and tag not in only:              # не просили обновлять — переносим как было
            if tag in prev_srcs:
                out_sources.append(prev_srcs[tag])
                out_nodes.extend(prev_nodes.get(tag, []))
            continue
        log(f"[fetch] {tag} ({sub['kind']})")
        try:
            items = sources.fetch_subscription(sub, ctx)
            nodes = [rawfmt.make_node(tag, item.get("tag", ""), item, item.get("_file_cc"))
                     for item in items]
            src = {"provider": tag, "kind": sub["kind"], "ok": True, "count": len(nodes),
                   "fetched_at": now_iso, "last_ok_at": now_iso, "stale": False, "error": None}
            meta = metamod.from_headers(ctx.response_headers)
            if meta:
                src["meta"] = meta
        except Exception as exc:  # noqa: BLE001 — любая ошибка подписки изолирована
            error = f"{exc.__class__.__name__}: {exc}"
            prev_src = prev_srcs.get(tag) or {}
            last_ok = prev_src.get("last_ok_at")
            last_ok_dt = parse_iso(last_ok)
            nodes = prev_nodes.get(tag, [])
            stale = bool(nodes) and last_ok_dt is not None and now - last_ok_dt <= stale_max
            if not stale:
                nodes = []
            src = {"provider": tag, "kind": sub["kind"], "ok": False, "count": len(nodes),
                   "fetched_at": now_iso, "last_ok_at": last_ok, "stale": stale, "error": error}
            if prev_src.get("meta"):            # метаданные — от последней удачной загрузки
                src["meta"] = prev_src["meta"]
            note = f"взяты прошлые ноды ({len(nodes)}, stale)" if stale else "нод нет"
            log(f"  [fetch] {tag}: СБОЙ — {error} → {note}")
        else:
            log(f"  [fetch] {tag}: нод {len(nodes)}")
        out_nodes.extend(nodes)
        out_sources.append(src)

    return {"version": rawfmt.RAW_VERSION, "generated_at": now_iso, "providers": name,
            "nodes_hash": rawfmt.nodes_hash(out_nodes), "sources": out_sources,
            "nodes": out_nodes}


def check_guard(raw, previous, min_ratio):
    """GuardError, если нод стало меньше min_ratio от прошлого raw."""
    if not previous:
        return
    before, after = len(previous.get("nodes", [])), len(raw["nodes"])
    if before and after < float(min_ratio) * before:
        raise GuardError(f"нод стало {after} против {before} в прошлый раз "
                         f"(< {min_ratio:.0%}) — файл не перезаписан; --force чтобы записать")


def summary(raw, previous=None, written=False, output=None):
    """Сводка прогона для --json / оркестратора (не часть контракта raw)."""
    types = {}
    for node in raw["nodes"]:
        per = types.setdefault(node["provider"], {})
        t = node["outbound"].get("type", "?")
        per[t] = per.get(t, 0) + 1
    srcs = [dict(s, types=types.get(s["provider"], {})) for s in raw["sources"]]
    return {"ok": all(s["ok"] for s in raw["sources"]), "written": written, "output": output,
            "nodes": len(raw["nodes"]),
            "prev_nodes": len(previous["nodes"]) if previous else None,
            "changed": previous is None or previous.get("nodes_hash") != raw["nodes_hash"],
            "nodes_hash": raw["nodes_hash"], "generated_at": raw["generated_at"],
            "sources": srcs}


def providers_name(path):
    return os.path.splitext(os.path.basename(path))[0]


__all__ = ["load_providers", "run", "check_guard", "summary", "ProvidersError", "GuardError",
           "FETCH_DEFAULTS", "CONFIG_STAGE_KEYS"]
