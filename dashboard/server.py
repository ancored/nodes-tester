"""Сборка веб-приложения админки и запуск сервера (stdlib http.server).

Каркас маршрутизации/статики — в [dashboard/webapp.py](dashboard/webapp.py); здесь
композиция: регистрируем read-маршруты (`api_read`), в full-режиме — control/config.
"""

from __future__ import annotations

from . import api_read
from .webapp import App, serve_forever


def build_app(cfg, *, runner=None, interval: int | None = None) -> App:
    """Собрать App для обоих режимов.

    runner=None — read-only (отдельный `python -m dashboard`);
    runner задан — full-режим (встроен в Runner): read + control/config.
    Токен/доступ берём из cfg.dashboard.
    """
    dash = getattr(cfg, "dashboard", None)
    app = App(
        cfg,
        runner=runner,
        interval=interval if interval is not None else (dash.interval if dash else 10),
        token=(dash.token if dash else ""),
        read_open=(dash.read_open if dash else True),
    )
    api_read.register(app)
    # Управление (карантин/бан/switch/статус/прогон/логи) — требует runner (409 без
    # него); редактор конфигов — файловый, работает и в read-only режиме (по токену).
    from . import api_control, api_config
    api_control.register(app)
    api_config.register(app)
    from . import api_boxdash, api_pipeline, api_session, api_singbox
    api_boxdash.register(app)
    api_pipeline.register(app)
    api_singbox.register(app)
    api_session.register(app)
    return app


def serve(cfg, host: str, port: int, interval: int) -> None:
    app = build_app(cfg, interval=interval)
    serve_forever(app, host, port)
