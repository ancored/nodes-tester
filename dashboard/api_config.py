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
import os
import tempfile

from .webapp import App, HttpError


def _config_path(app: App) -> str:
    path = getattr(app.cfg, "path", "") or ""
    if not path:
        raise HttpError(500, "путь конфига неизвестен (config.path не задан)")
    return os.path.abspath(path)


def _cfg_dir(app: App) -> str:
    return os.path.dirname(_config_path(app))


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
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
    subscribes = data.get("subscribes")
    output = data.get("save_config_path")
    if not isinstance(subscribes, list):
        raise HttpError(400, "providers.subscribes должен быть массивом")
    if not isinstance(output, str) or not output.strip():
        raise HttpError(400, "providers.save_config_path должен быть непустой строкой")
    if not all(isinstance(item, dict) for item in subscribes):
        raise HttpError(400, "каждый элемент providers.subscribes должен быть объектом")


def register(app: App) -> None:
    @app.route("GET", "/api/config", needs_token=True)
    def get_config(app, req):
        path = _config_path(app)
        return {"path": path, "data": _read_json(path)}

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
        _atomic_write(path, text)
        # Применение в работающем тестере требует перезапуска процесса (рефактор
        # re-init Runner) — сообщаем честно, а не делаем «тихий» hot-reload.
        return {"ok": True, "path": path,
                "restart_required": getattr(app, "runner", None) is not None}

    @app.route("GET", "/api/config/providers", needs_token=True)
    def get_providers(app, req):
        path = os.path.join(_cfg_dir(app), "providers.json")
        return {"path": path, "data": _read_json(path)}

    @app.route("PUT", "/api/config/providers", needs_token=True)
    def put_providers(app, req):
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "providers.json должен быть объектом")
        _validate_providers(data)
        path = os.path.join(_cfg_dir(app), "providers.json")
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write(path, text)
        return {"ok": True, "path": path}
