"""Редактор единого config.json: GET/PUT с проверкой и атомарной записью.

Конфиг содержит секреты (api.secret, admin.token, токены уведомлений, URL подписок),
поэтому ВСЕ эндпоинты модуля требуют X-Admin-Token. Тело проверяется тем же загрузчиком,
что у тестера (nodes_tester.config.parse_config); при ошибке файл не трогается. Изменения
действуют после перезапуска тестера — админка встроенного тестера на OpenWrt перезапускает его
сама. Соединения пользователей при этом не рвутся: на несколько секунд останавливаются только
мониторинг и тестирование.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

from nodes_common.fileio import atomic_write_private, dumps_json
from nodes_tester.config import WRITE_LOCK

from .webapp import App, HttpError

TESTER_INIT = "/etc/init.d/nodes-tester"


def _restart_tester(app: App) -> str:
    """Перезапустить встроенный тестер после сохранения. Пусто — перезапуск запущен, иначе
    причина, почему перезапускать нужно вручную.

    Перезапуск идёт отвязанным процессом через секунду: ответ на PUT успевает уйти, а
    процесс переживает остановку тестера (своя сессия). Во время конвейера не перезапускаем:
    оркестратор живёт в тестере, и прерванное применение осталось бы без надзора."""
    runner = getattr(app, "runner", None)
    if runner is None or not os.path.exists(TESTER_INIT):
        return "перезапустите тестер вручную"
    orch = getattr(runner, "orchestrator", None)
    if orch is not None and orch.status()["current"]:
        return "идёт конвейер — перезапустите тестер после его завершения"
    subprocess.Popen(["/bin/sh", "-c", f"sleep 1; {TESTER_INIT} restart"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ""


def _save(app: App, path: str, data: dict) -> dict:
    """Проверить загрузчиком тестера, записать атомарно с правами 0600, перезапустить тестер.
    Вызывается под WRITE_LOCK."""
    from nodes_tester.config import ConfigError, parse_config
    try:
        parse_config(data)
    except ConfigError as exc:
        raise HttpError(400, f"конфиг невалиден: {exc}") from exc
    atomic_write_private(path, dumps_json(data))
    note = _restart_tester(app)
    return {"ok": True, "path": path, "revision": _revision(path), "restart_required": True,
            "restarting": not note, "restart_note": note}


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


def _keys_by_provider(tags) -> dict[str, set]:
    from naming import parse_node
    out: dict[str, set] = {}
    for tag in tags:
        ident = parse_node(tag)
        if ident.node_id:
            out.setdefault(ident.provider, set()).add(ident.node_id)
    return out


def _diff(app: App, raw: dict) -> dict:
    """Разница последнего опроса с действующим конфигом роутера по подпискам: сколько нод в
    конфиге, сколько дала бы сборка из опроса, новых и ушедших доступов."""
    from nodes_config import build
    try:
        fragment, _ = build.build([raw], app.cfg, log=lambda m: None)
        with open(app.cfg.nodes_file, encoding="utf-8") as fh:
            current = json.load(fh)
    except (OSError, ValueError, KeyError, TypeError):     # нет файла, битый raw или nodes.json
        return {}
    fresh = _keys_by_provider(build.tags(fragment))
    config = _keys_by_provider(build.tags(current))
    return {provider: {"in_config": len(config.get(provider, ())), "in_poll": len(fresh.get(provider, ())),
                       "new": len(fresh.get(provider, set()) - config.get(provider, set())),
                       "gone": len(config.get(provider, set()) - fresh.get(provider, set()))}
            for provider in set(fresh) | set(config)}


def _subscription_states(app: App) -> dict:
    """Состояние подписок по последнему опросу (raw/router.json): tag → {ok, count, last_ok_at,
    stale, error, meta, diff}; diff — разница с действующим конфигом. Без оркестратора (нет
    каталога данных) — пусто."""
    orch = getattr(getattr(app, "runner", None), "orchestrator", None)
    if orch is None:
        return {}
    try:
        with open(os.path.join(str(orch.data_dir), "raw", "router.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        sources = raw.get("sources") or []
    except (OSError, ValueError, AttributeError):
        return {}
    diff = _diff(app, raw)
    out = {}
    for src in sources:
        if isinstance(src, dict) and src.get("provider"):
            state = {k: src.get(k) for k in ("ok", "count", "fetched_at", "last_ok_at", "stale",
                                             "error", "meta")}
            state["diff"] = diff.get(src["provider"])
            out[src["provider"]] = state
    return out


def _client_candidates(app: App) -> "dict | None":
    """Кандидаты клиентских групп от тестера: {ts, groups: {группа: число нод}}; нет файла — None."""
    try:
        with open(app.cfg.client_candidates_file, encoding="utf-8") as fh:
            data = json.load(fh)
        groups = data.get("groups") or {}
        return {"ts": data.get("ts"), "groups": {g: len(tags) for g, tags in groups.items()}}
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def register(app: App) -> None:
    initial_revision = _revision(_config_path(app))

    @app.route("GET", "/api/config", needs_token=True)
    def get_config(app, req):
        path = _config_path(app)
        with WRITE_LOCK:
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
        """Весь файл (расширенный JSON-редактор); If-Match — ревизия открытого файла."""
        data = req.json()                      # HttpError 400 при битом JSON
        if not isinstance(data, dict):
            raise HttpError(400, "config.json должен быть объектом")
        path = _config_path(app)
        with WRITE_LOCK:
            expected = req.headers.get("If-Match")
            if expected and expected != _revision(path):
                raise HttpError(409, "Файл изменился после открытия. Перечитайте его перед сохранением.")
            return _save(app, path, data)

    @app.route("POST", "/api/config/sections", needs_token=True)
    def put_sections(app, req):
        """Только изменённые разделы верхнего уровня (форма): {"base": {раздел: как было
        открыто}, "set": {раздел: новое значение}}; null — раздела нет. Конфликт — только если
        по SSH или в другом окне изменили те же разделы: подписки и группы правятся параллельно."""
        body = req.json()
        base, new = body.get("base"), body.get("set")
        if not isinstance(base, dict) or not isinstance(new, dict) or not new:
            raise HttpError(400, "нужны объекты base и set")
        path = _config_path(app)
        with WRITE_LOCK:
            data = _document(path)["data"]
            changed = [key for key in new if data.get(key) != base.get(key)]
            if changed:
                raise HttpError(409, f"Раздел {changed[0]} изменился после открытия. Перечитайте файл "
                                     f"перед сохранением.")
            for key, value in new.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            return _save(app, path, data)

    @app.route("GET", "/api/config/subscription-options", needs_token=True)
    def subscription_options(app, req):
        from nodes_admin import clients
        from nodes_fetch import device, happ_keys
        from nodes_fetch.happ import _HEADERS
        from nodes_fetch.util import DEFAULT_UA
        from nodes_tester.config import BUILTIN_REGIONS, CHECKS, CLIENT_CHECKS, IP_KINDS
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
            "sources": _subscription_states(app),
            "client_checks": list(CLIENT_CHECKS),
            "clients": clients.state(app.cfg, os.path.join(os.path.dirname(config_path), "singbox")),
            "client_groups": _client_candidates(app)}
