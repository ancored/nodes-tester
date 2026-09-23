"""Write/control-эндпоинты админки (Фаза 2–3).

Работают только во встроенном режиме (App.runner задан, т.е. сервер поднят внутри
процесса тестера) и требуют заголовок X-Admin-Token. В read-only режиме
(`python -m dashboard`) отвечают 409 tester_not_running.

Действия напрямую дёргают живые объекты Runner/Switcher/Storage (они уже сериализуют
доступ внутренними локами) — никаких IPC/командных таблиц в БД не заводим.
"""

from __future__ import annotations

import re
import time

from .webapp import App, HttpError


def _runner(app: App):
    runner = getattr(app, "runner", None)
    if runner is None:
        raise HttpError(409, "tester_not_running: управление доступно только во встроенном режиме")
    return runner


def _storage_node(app: App, crc: str, action: str):
    r = _runner(app)
    if r.storage is None:
        raise HttpError(409, f"storage отключён — {action} недоступен")
    if not re.fullmatch(r"[0-9a-fA-F]{8}", crc or ""):
        raise HttpError(400, "crc должен состоять из 8 hex-символов")
    if not r.storage.node_exists(crc):
        raise HttpError(404, "нода с таким crc не найдена")
    return r, r.storage


def register(app: App) -> None:
    open_read = app.read_open

    # --- Карантин / снятие / бан ноды (по CRC) --------------------------

    @app.route("POST", "/api/nodes/{crc}/quarantine", needs_token=True)
    def quarantine(app, req, crc):
        r, storage = _storage_node(app, crc, "карантин")
        until = int(time.time() + r.cfg.cooldown.garbage_hours * 3600)
        # Ручной карантин = мусорная нода на весь срок garbage_hours.
        storage.set_backoff(crc, until, r.cfg.cooldown.max_skip + 1, "garbage")
        return {"ok": True, "until": until}

    @app.route("POST", "/api/nodes/{crc}/unquarantine", needs_token=True)
    def unquarantine(app, req, crc):
        _r, storage = _storage_node(app, crc, "снятие карантина")
        storage.clear_backoff(crc)
        return {"ok": True}

    @app.route("POST", "/api/nodes/{crc}/ban", needs_token=True)
    def ban(app, req, crc):
        _r, storage = _storage_node(app, crc, "бан")
        storage.set_banned(crc, True)
        return {"ok": True}

    @app.route("POST", "/api/nodes/{crc}/unban", needs_token=True)
    def unban(app, req, crc):
        _r, storage = _storage_node(app, crc, "разбан")
        storage.set_banned(crc, False)
        return {"ok": True}

    # --- Форс-переключение активной ноды региона ------------------------

    @app.route("POST", "/api/regions/{region}/switch", needs_token=True)
    def force_switch(app, req, region):
        r = _runner(app)
        if r.switcher is None:
            raise HttpError(409, "switching отключён — форс-переключение недоступно")
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "JSON-тело должно быть объектом")
        node = data.get("node")
        if not isinstance(node, str) or not node.strip():
            raise HttpError(400, "поле 'node' обязательно и должно быть строкой")
        node = node.strip()
        ok = r.switcher.force_activate(region, node)
        if not ok:
            raise HttpError(409, "не удалось переключить: нода не кандидат региона или PUT не прошёл")
        return {"ok": True, "region": region, "node": node}

    # --- Статус / внеплановый прогон / живой лог ------------------------

    @app.route("GET", "/api/status", needs_token=not open_read)
    def status(app, req):
        return _runner(app).status()

    @app.route("POST", "/api/run/pass", needs_token=True)
    def run_pass(app, req):
        _runner(app).request_pass()
        return {"ok": True}

    @app.route("GET", "/api/logs", needs_token=not open_read)
    def logs(app, req):
        r = _runner(app)
        try:
            seq = int(req.q("seq", 0) or 0)
            tail = int(req.q("tail", 300) or 300)
        except (TypeError, ValueError) as exc:
            raise HttpError(400, "seq и tail должны быть целыми числами") from exc
        if seq < 0 or not 1 <= tail <= 2000:
            raise HttpError(400, "seq должен быть >= 0, tail — от 1 до 2000")
        entries = r.log.snapshot()
        if seq > 0:
            entries = [e for e in entries if e["seq"] > seq]
        else:
            entries = entries[-tail:]
        return {"seq": r.log.last_seq(), "lines": entries}
