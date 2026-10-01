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
        if not r.cfg.cooldown.enabled:
            raise HttpError(409, "Ограничения проверок отключены в cooldown.enabled. Карантин не будет действовать.")
        until = int(time.time() + r.cfg.cooldown.garbage_hours * 3600)
        streak = r.cfg.cooldown.max_skip + 1
        # Ручной карантин = мусорная нода на весь срок garbage_hours.
        storage.set_backoff(crc, until, streak, "garbage")
        storage.add_node_event(crc, "garbage", "manual")
        # Сразу вне кандидатов переключателя; активная — заменяется.
        r.restrict_node(crc, True, (until, 0, streak, "garbage"))
        return {"ok": True, "until": until}

    @app.route("POST", "/api/nodes/{crc}/unquarantine", needs_token=True)
    def unquarantine(app, req, crc):
        r, storage = _storage_node(app, crc, "снятие карантина")
        storage.clear_backoff(crc)
        if crc not in storage.banned_crcs():
            r.restrict_node(crc, False)
        storage.add_node_event(crc, "restriction_cleared", "manual")
        return {"ok": True}

    @app.route("POST", "/api/nodes/{crc}/ban", needs_token=True)
    def ban(app, req, crc):
        r, storage = _storage_node(app, crc, "бан")
        storage.set_banned(crc, True)
        r.restrict_node(crc, True)
        storage.add_node_event(crc, "ban", "manual")
        return {"ok": True}

    @app.route("POST", "/api/nodes/{crc}/unban", needs_token=True)
    def unban(app, req, crc):
        r, storage = _storage_node(app, crc, "разбан")
        storage.set_banned(crc, False)
        if crc not in r.restricted_crcs():
            r.restrict_node(crc, False)
        storage.add_node_event(crc, "unban", "manual")
        return {"ok": True}

    # --- Форс-переключение активной ноды группы ------------------------

    def force_switch(app, req, group):
        r = _runner(app)
        if r.switcher is None:
            raise HttpError(409, "switching отключён — форс-переключение недоступно",
                            {"code": "switching_disabled"})
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "JSON-тело должно быть объектом")
        node = data.get("node")
        if not isinstance(node, str) or not node.strip():
            raise HttpError(400, "поле 'node' обязательно и должно быть строкой")
        node = node.strip()
        if r.storage is not None:
            from naming import parse_node
            crc = parse_node(node).node_id
            if crc and (not r.storage.node_is_present(crc) or crc in r.storage.banned_crcs() or crc in r.restricted_crcs()):
                raise HttpError(409, "Нода исключена или находится на паузе/в карантине.",
                                {"code": "node_restricted"})
        ok = r.switcher.force_activate(group, node)
        if not ok:
            board = getattr(r, "board", None)
            allowed = ([g for g in board.regions()
                        if any(c.get("node") == node for c in board.candidates(g))]
                       if board is not None else [])
            if group in allowed:
                raise HttpError(409, f"Не удалось выбрать ноду в sing-box для группы {group}.",
                                {"code": "switch_failed", "allowed_groups": allowed})
            hint = f" Допустимые группы: {', '.join(allowed)}." if allowed else ""
            raise HttpError(409, f"Нода не кандидат группы {group}.{hint}",
                            {"code": "not_candidate", "allowed_groups": allowed})
        return {"ok": True, "group": group, "region": group, "node": node, "temporary": True}

    app.route("POST", "/api/groups/{group}/switch", needs_token=True)(force_switch)
    # Прежний путь (до настраиваемых групп): {region} — то же имя группы.
    app.route("POST", "/api/regions/{group}/switch", needs_token=True)(force_switch)

    # --- sing-box: остановка/запуск, killswitch -------------------------

    def _singbox(app):
        ctl = getattr(_runner(app), "singbox", None)
        if ctl is None:
            raise HttpError(409, "управление sing-box недоступно")
        return ctl

    def _do(fn, *args):
        from nodes_tester.singbox_ctl import ControlError
        try:
            return fn(*args)
        except ControlError as exc:
            raise HttpError(409, str(exc)) from exc
        except OSError as exc:
            raise HttpError(500, f"{type(exc).__name__}: {exc}") from exc

    @app.route("GET", "/api/singbox/control", needs_token=not open_read)
    def singbox_status(app, req):
        return _singbox(app).status()

    @app.route("POST", "/api/singbox/stop", needs_token=True)
    def singbox_stop(app, req):
        return _do(_singbox(app).stop)

    @app.route("POST", "/api/singbox/start", needs_token=True)
    def singbox_start(app, req):
        return _do(_singbox(app).start)

    @app.route("PUT", "/api/singbox/killswitch", needs_token=True)
    def singbox_killswitch(app, req):
        data = req.json()
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            raise HttpError(400, "нужно {\"enabled\": true|false}")
        return _do(_singbox(app).set_killswitch, data["enabled"])

    # --- Уведомления -----------------------------------------------------

    @app.route("POST", "/api/notify/test", needs_token=True)
    def notify_test(app, req):
        return _runner(app).notifier.test()

    # --- Статус / внеплановый прогон / живой лог ------------------------

    @app.route("GET", "/api/status", needs_token=not open_read)
    def status(app, req):
        return _runner(app).status()

    @app.route("POST", "/api/run/pass", needs_token=True)
    def run_pass(app, req):
        r = _runner(app)
        if not r.status().get("running"):
            raise HttpError(409, "Тестер остановлен. Запрос проверки не запускает сервис.")
        queued = r.request_pass()
        return {"ok": True, "queued": True, "already_queued": queued is False}

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
