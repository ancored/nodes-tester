"""Редактор единого config.json: GET/PUT с проверкой и атомарной записью.

Конфиг содержит секреты (api.secret, admin.token, токены уведомлений, URL подписок),
поэтому ВСЕ эндпоинты модуля требуют X-Admin-Token. Тело проверяется тем же загрузчиком,
что у тестера (nodes_tester.config.parse_config); при ошибке файл не трогается. Изменения
действуют после перезапуска тестера — админка перезапускает его сама (api_session).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

from nodes_common.fileio import atomic_write_private, dumps_json

from .webapp import App, HttpError

_edit_lock = threading.Lock()


def _revision(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return "missing"


def _config_path(app: App) -> str:
    path = getattr(app.cfg, "path", "") or ""
    if not path:
        raise HttpError(500, "путь конфига неизвестен (config.path не задан)")
    return os.path.abspath(path)


def _document(path: str):
    # Data and revision must describe the same bytes, even if an SSH editor writes
    # the file while this request is running.
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return {"path": path, "data": json.loads(raw.decode("utf-8")),
                "revision": hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError) as exc:
        raise HttpError(500, f"Не удалось прочитать файл {path}: {exc}")


def _subscription_states(app: App) -> dict:
    """Состояние подписок по последнему raw (raw/router.json, raw/clients.json): tag →
    {ok, count, last_ok_at, stale, error, meta}. Без оркестратора (нет каталога данных) — пусто."""
    orch = getattr(getattr(app, "runner", None), "orchestrator", None)
    if orch is None:
        return {}
    folder = os.path.join(str(orch.data_dir), "raw")
    out = {}
    for name in ("router.json", "clients.json"):
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                sources = json.load(fh).get("sources") or []
        except (OSError, ValueError, AttributeError):
            continue
        for src in sources:
            if isinstance(src, dict) and src.get("provider"):
                out[src["provider"]] = {k: src.get(k) for k in
                                        ("ok", "count", "fetched_at", "last_ok_at", "stale",
                                         "error", "meta")}
    return out


def register(app: App) -> None:
    initial_revision = _revision(_config_path(app))

    @app.route("GET", "/api/config", needs_token=True)
    def get_config(app, req):
        path = _config_path(app)
        with _edit_lock:
            result = _document(path)
            result["restart_required"] = result["revision"] != initial_revision
            return result

    @app.route("GET", "/api/config/schema", needs_token=True)
    def get_schema(app, req):
        # Схема лежит рядом с config.json (на неё ссылается поле $schema).
        schema_path = os.path.join(os.path.dirname(_config_path(app)), "config.schema.json")
        try:
            with open(schema_path, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError as exc:
            raise HttpError(500, f"схема не найдена: {exc}")

    @app.route("PUT", "/api/config", needs_token=True)
    def put_config(app, req):
        from nodes_tester.config import ConfigError, parse_config
        data = req.json()                      # HttpError 400 при битом JSON
        if not isinstance(data, dict):
            raise HttpError(400, "config.json должен быть объектом")
        try:
            parse_config(data)
        except ConfigError as exc:
            raise HttpError(400, f"конфиг невалиден: {exc}") from exc
        path = _config_path(app)
        with _edit_lock:
            expected = req.headers.get("If-Match")
            if expected and expected != _revision(path):
                raise HttpError(409, "Файл изменился после открытия. Перечитайте его перед сохранением.")
            atomic_write_private(path, dumps_json(data))
            revision = _revision(path)
        return {"ok": True, "path": path, "revision": revision, "restart_required": True}

    @app.route("GET", "/api/config/subscription-options", needs_token=True)
    def subscription_options(app, req):
        from nodes_fetch import device, happ_keys
        from nodes_fetch.happ import _HEADERS
        from nodes_fetch.util import DEFAULT_UA
        from nodes_tester.config import BUILTIN_REGIONS, CHECKS, IP_KINDS
        config_path = _config_path(app)
        device_path = device.default_path(config_path)
        hwid = device.read(device_path).get("hwid") or ""
        happ_headers = dict(_HEADERS)
        if hwid:
            happ_headers["X-Hwid"] = hwid
        return {"user_agents": [
            {"value": "", "label": "По умолчанию (Safari)", "effective": DEFAULT_UA},
            {"value": "curl", "label": "curl"},
            {"value": "clashmeta", "label": "Clash Meta (clashmeta)"},
        ], "happ_headers": happ_headers,
            "builtin_regions": BUILTIN_REGIONS, "checks": list(CHECKS), "ip_kinds": list(IP_KINDS),
            "device": {"hwid": hwid, "path": device_path},
            "happ_keys": {"default_url": happ_keys.DEFAULT_URL,
                          "path": happ_keys.default_path(config_path)},
            "branches": ["router", "clients"],
            "sources": _subscription_states(app)}
