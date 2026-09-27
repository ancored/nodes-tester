"""Редактор конфигов/подписок (Фаза 4): config.json / providers.json через UI.

GET/PUT с валидацией и атомарной записью (temp + rename). Конфиг содержит секрет
(clash_api.secret), поэтому ВСЕ эндпоинты этого модуля требуют X-Admin-Token.

Валидация config.json переиспользует существующий `load_config` (JSON Schema при
наличии jsonschema + семантическая `_validate`): тело пишется во временный файл в
той же папке (чтобы `$schema` разрешился) и прогоняется через load_config; при
ошибке файл не трогается.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading

from .webapp import App, HttpError

_edit_lock = threading.Lock()


def _revision(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return "missing"


def _save(req, path, text):
    with _edit_lock:
        expected = req.headers.get("If-Match")
        if expected and expected != _revision(path):
            raise HttpError(409, "Файл изменился после открытия. Перечитайте его перед сохранением.")
        _atomic_write(path, text)
        return _revision(path)


def _config_path(app: App) -> str:
    path = getattr(app.cfg, "path", "") or ""
    if not path:
        raise HttpError(500, "путь конфига неизвестен (config.path не задан)")
    return os.path.abspath(path)


def _cfg_dir(app: App) -> str:
    return os.path.dirname(_config_path(app))


def _providers_path(app: App) -> str:
    """Файл подписок редактора: dashboard.providers_file (относительный — от папки
    config.json) либо providers.json рядом с config.json."""
    dash = getattr(app.cfg, "dashboard", None)
    configured = (getattr(dash, "providers_file", "") or "").strip()
    if not configured:
        return os.path.join(_cfg_dir(app), "providers.json")
    return os.path.abspath(os.path.join(_cfg_dir(app), configured))


def _groups_path(app: App) -> str:
    configured = (getattr(app.cfg.dashboard, "groups_file", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.join(_cfg_dir(app), configured))
    return os.path.join(os.path.dirname(_providers_path(app)), "groups_params.json")


def _validate_groups(data):
    from nodes_config.params import load
    try:
        load(data)
        for key in ("raw_user_nodes",):
            if key in data and not isinstance(data[key], bool):
                raise ValueError(f"{key}: ожидается boolean")
        emit = data.get("emit", {})
        for key in ("nodes_tester", "global_failsafe"):
            if key in emit and not isinstance(emit[key], bool):
                raise ValueError(f"emit.{key}: ожидается boolean")
        if any(r not in ("eu", "us", "ru", "other") for r in emit.get("ensure_regions", [])):
            raise ValueError("emit.ensure_regions: допустимы eu, us, ru, other")
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas", "groups_params.schema.json")
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            with open(schema_path, encoding="utf-8") as fh:
                schema = json.load(fh)
            jsonschema.validate(data, schema)
    except (ValueError, TypeError) as exc:
        raise HttpError(400, f"groups_params невалиден: {exc}") from exc
    except Exception as exc:
        # jsonschema.ValidationError does not inherit ValueError.
        if exc.__class__.__name__ == "ValidationError":
            raise HttpError(400, f"groups_params невалиден: {exc.message}") from exc
        raise


def _atomic_write(path: str, text: str) -> None:
    # Симлинк сохраняется: пишем в файл, на который он указывает (os.replace по самому
    # симлинку заменил бы его обычным файлом, и nodes_fetch читал бы старую цель).
    path = os.path.realpath(path)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise HttpError(500, f"не удалось прочитать {path}: {exc}")


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


def _validate_config_body(app: App, text: str) -> None:
    """Прогнать тело конфига через load_config (schema + семантическая валидация)."""
    from nodes_tester.config import load_config

    d = _cfg_dir(app)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nt-validate-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            load_config(tmp)
        except (FileNotFoundError, ValueError) as exc:
            raise HttpError(400, f"конфиг невалиден: {exc}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _validate_providers(data: dict) -> None:
    """Та же проверка, что у nodes_fetch (v2 и совместимый v1): subscribes, уникальные
    tag, у каждой подписки url/file/type=folder, корректный fetch.min_ratio."""
    from nodes_fetch.fetch import load_providers

    try:
        load_providers(data, log=lambda *_: None)
    except (ValueError, TypeError) as exc:
        raise HttpError(400, f"providers невалиден: {exc}")


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
        schema_path = os.path.join(_cfg_dir(app), "config.schema.json")
        try:
            with open(schema_path, encoding="utf-8") as fh:
                schema = json.load(fh)
        except OSError as exc:
            raise HttpError(500, f"схема не найдена: {exc}")
        return schema

    @app.route("PUT", "/api/config", needs_token=True)
    def put_config(app, req):
        data = req.json()                      # HttpError 400 при битом JSON
        if not isinstance(data, dict):
            raise HttpError(400, "config.json должен быть объектом")
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _validate_config_body(app, text)
        path = _config_path(app)
        revision = _save(req, path, text)
        # Применение в работающем тестере требует перезапуска процесса (рефактор
        # re-init Runner) — сообщаем честно, а не делаем «тихий» hot-reload.
        return {"ok": True, "path": path,
                "revision": revision, "restart_required": True}

    @app.route("GET", "/api/config/providers", needs_token=True)
    def get_providers(app, req):
        path = _providers_path(app)
        with _edit_lock:
            return {**_document(path), "application_state": "unknown"}

    @app.route("PUT", "/api/config/providers", needs_token=True)
    def put_providers(app, req):
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "providers.json должен быть объектом")
        _validate_providers(data)
        path = _providers_path(app)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        revision = _save(req, path, text)
        return {"ok": True, "path": path, "revision": revision,
                "application_state": "not_applied_by_dashboard"}

    @app.route("GET", "/api/config/subscription-options", needs_token=True)
    def subscription_options(app, req):
        from nodes_fetch.util import DEFAULT_UA
        from nodes_fetch.happ import _HEADERS
        from nodes_config.params import load
        from nodes_config.groups import DEFAULT_ENSURE_REGIONS
        group_defaults = load()
        group_defaults["emit"] = {"nodes_tester": True, "global_failsafe": False,
                                  "ensure_regions": list(DEFAULT_ENSURE_REGIONS)}
        return {"user_agents": [
            {"value": "", "label": "По умолчанию (Safari)", "effective": DEFAULT_UA},
            {"value": "curl", "label": "curl"},
            {"value": "clashmeta", "label": "Clash Meta (clashmeta)"},
        ], "happ_headers": dict(_HEADERS), "group_defaults": group_defaults}

    @app.route("GET", "/api/config/groups", needs_token=True)
    def get_groups(app, req):
        path = _groups_path(app)
        with _edit_lock:
            result = _document(path) if os.path.exists(path) else {"path": path, "data": {}, "revision": "missing"}
        return {**result, "application_state": "unknown"}

    @app.route("PUT", "/api/config/groups", needs_token=True)
    def put_groups(app, req):
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "groups_params.json должен быть объектом")
        _validate_groups(data)
        path = _groups_path(app)
        revision = _save(req, path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return {"ok": True, "path": path, "revision": revision,
                "application_state": "not_applied_by_dashboard"}
