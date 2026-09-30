"""Официальная Dashboard API-сервиса sing-box с origin админки.

Dashboard хранит адрес и секрет сервера в localStorage своего origin и не принимает их
через URL. Поэтому админка отдаёт её статику по /sing-box-dashboard/ (прокси к
{box_api.url}/dashboard/), а SPA перед открытием записывает сервер в localStorage.
Запросы самой Dashboard идут напрямую в API sing-box (он отвечает с CORS `*`).
"""

from __future__ import annotations

import posixpath
import urllib.error
import urllib.request

from .webapp import App, HttpError, Response

PREFIX = "/sing-box-dashboard/"


def _fetch(app: App, path: str) -> Response:
    rel = posixpath.normpath("/" + path).lstrip("/")
    if rel.startswith(".."):
        raise HttpError(404, "not found")
    base = app.cfg.box_api.url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/dashboard/{rel}", timeout=10) as resp:
            return Response(200, resp.headers.get("Content-Type") or "application/octet-stream", resp.read())
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, f"sing-box dashboard: HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise HttpError(502, f"sing-box API недоступен ({base}): {exc}") from exc


def register(app: App) -> None:
    @app.route("GET", "/api/singbox/dashboard", needs_token=True)
    def server(app, req):
        cfg = app.cfg.box_api
        return {"url": cfg.url, "secret": cfg.secret or "", "path": PREFIX}

    @app.route("GET", PREFIX)
    def index(app, req):
        return _fetch(app, "")

    @app.route("GET", PREFIX + "{path...}")
    def asset(app, req, path):
        return _fetch(app, path)
