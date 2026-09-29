"""Минимальный HTTP-каркас админки поверх stdlib (без внешних зависимостей).

`App` — таблица маршрутов (метод+regex пути → хендлер) + раздача статики SPA из
`dashboard/static/` + JSON-хелперы + проверка токена для write/control-эндпоинтов.
Один `App` обслуживает оба режима:

- read-only (отдельный `python -m dashboard`): `runner is None`, доступны только read;
- full (встроен в процесс тестера): `runner` задан → доступны и control/config.

Хендлеры регистрируются в отдельных модулях (`api_read`, позже `api_control`,
`api_config`) через `App.route(...)`. Каркас проектно ничего не знает о предметной
области — только маршрутизация, статика, JSON и токен.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Тип контента по расширению для раздачи собранного Vite-бандла.
_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
    ".webp": "image/webp", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",
}


class HttpError(Exception):
    """Хендлер бросает это, чтобы вернуть код ошибки с JSON-телом {error: ...}."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    """Разобранный запрос, который получает хендлер."""

    def __init__(self, method: str, path: str, query: dict, headers, body: bytes):
        self.method = method
        self.path = path                 # путь без query
        self.query = query               # {ключ: [значения]}
        self.headers = headers
        self.body = body

    def q(self, key: str, default=None):
        vals = self.query.get(key)
        return vals[0] if vals else default

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(400, f"невалидный JSON в теле: {exc}") from exc

    def token(self) -> str:
        return self.headers.get("X-Admin-Token", "") or ""


class Response:
    """Ответ хендлера. Хендлер может вернуть Response, либо dict/list (→ JSON 200)."""

    def __init__(self, status: int, ctype: str, body: bytes):
        self.status = status
        self.ctype = ctype
        self.body = body

    @classmethod
    def json(cls, data, status: int = 200) -> "Response":
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return cls(status, "application/json; charset=utf-8", body)

    @classmethod
    def text(cls, text: str, status: int = 200,
             ctype: str = "text/plain; charset=utf-8") -> "Response":
        return cls(status, ctype, text.encode("utf-8"))


class _Route:
    __slots__ = ("method", "regex", "fn", "needs_token")

    def __init__(self, method: str, pattern: str, fn, needs_token: bool):
        self.method = method.upper()
        # {name} в пути → именованная группа (без слэшей).
        rx = re.sub(r"\{(\w+)\.\.\.\}", r"(?P<\1>.+)", pattern)
        rx = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", rx)
        self.regex = re.compile("^" + rx + "$")
        self.fn = fn
        self.needs_token = needs_token


class App:
    """Композиция маршрутов + статика. Строится в `server.build_app`."""

    def __init__(self, cfg, *, runner=None, interval: int = 10,
                 token: str = "", read_open: bool = True,
                 static_dir: str = _STATIC_DIR):
        self.cfg = cfg
        self.runner = runner            # None = read-only режим
        self.interval = interval
        self.token = token or ""
        self.read_open = read_open
        self.static_dir = static_dir
        self._routes: list[_Route] = []
        # Фолбэк-рендерер для '/', пока нет собранного SPA (легаси-страница).
        self.legacy_index = None        # callable(app) -> Response | None

    # --- Регистрация маршрутов ---------------------------------------------

    def route(self, method: str, pattern: str, needs_token: bool = False):
        """Декоратор: app.route('GET', '/api/rating')(fn)."""
        def deco(fn):
            self._routes.append(_Route(method, pattern, fn, needs_token))
            return fn
        return deco

    def add_route(self, method: str, pattern: str, fn, needs_token: bool = False):
        self._routes.append(_Route(method, pattern, fn, needs_token))

    # --- Диспетчеризация ----------------------------------------------------

    def handle(self, method: str, raw_path: str, headers, body: bytes) -> Response:
        split = urlsplit(raw_path)
        path = split.path
        path_exists = False
        for route in self._routes:
            m = route.regex.match(path)
            if not m:
                continue
            path_exists = True
            if route.method != method:
                continue
            req = Request(method, path, parse_qs(split.query), headers, body)
            try:
                if route.needs_token:
                    self._check_token(req)
                result = route.fn(self, req, **m.groupdict())
            except HttpError as exc:
                return Response.json({"error": exc.message}, status=exc.status)
            if isinstance(result, Response):
                return result
            return Response.json(result)          # dict/list → JSON 200
        if path_exists:
            return Response.json({"error": "method not allowed"}, status=405)
        # Не API — пробуем статику / SPA-фолбэк (только на GET).
        if method == "GET" and not path.startswith("/api/"):
            return self._serve_static(path)
        return Response.json({"error": "not found"}, status=404)

    def _check_token(self, req: Request) -> None:
        if not self.token:
            raise HttpError(403, "admin token не настроен — write/control отключены")
        if req.token() != self.token:
            raise HttpError(401, "неверный или отсутствующий X-Admin-Token")

    # --- Статика / SPA ------------------------------------------------------

    def _serve_static(self, path: str) -> Response:
        index = os.path.join(self.static_dir, "index.html")
        has_spa = os.path.isfile(index)
        if path in ("/", "/index.html"):
            if has_spa:
                return self._file(index)
            if self.legacy_index is not None:      # легаси-страница до появления SPA
                resp = self.legacy_index(self)
                if resp is not None:
                    return resp
            return Response.text("nodes-tester: SPA ещё не собрана (web/ → npm run build)",
                                 status=200, ctype="text/html; charset=utf-8")
        # Обычный файл из static/. Оба вида разделителей нормализуем, затем
        # проверяем итоговый canonical path: одной строковой проверки `..` на Windows
        # недостаточно (`\\` там является разделителем каталогов).
        rel = posixpath.normpath(path.lstrip("/").replace("\\", "/"))
        root = os.path.realpath(self.static_dir)
        full = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
        try:
            inside = os.path.commonpath((root, full)) == root
        except ValueError:                       # разные диски/тома на Windows
            inside = False
        if rel.startswith("..") or not inside:
            return Response.json({"error": "not found"}, status=404)
        if os.path.isfile(full):
            return self._file(full)
        # SPA client-side routing: только URL без расширения получают index.html.
        # Иначе отсутствующий JS/CSS нельзя маскировать HTML-ответом с HTTP 200.
        if has_spa and not os.path.splitext(rel)[1]:
            return self._file(index)
        return Response.json({"error": "not found"}, status=404)

    def _file(self, full: str) -> Response:
        ext = os.path.splitext(full)[1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as fh:
                return Response(200, ctype, fh.read())
        except OSError:
            return Response.json({"error": "not found"}, status=404)


# --- HTTP-сервер поверх App ------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _dispatch(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 2 * 1024 * 1024:
                raise ValueError("Content-Length вне допустимого диапазона")
            body = self.rfile.read(length) if length > 0 else b""
            resp = self.server.app.handle(method, self.path, self.headers, body)
        except ValueError as exc:
            resp = Response.json({"error": f"bad request: {exc}"}, status=400)
        except Exception as exc:                    # noqa: BLE001 — не роняем сервер
            resp = Response.json({"error": f"internal: {exc}"}, status=500)
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(resp.body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, *a):
        pass                                        # без шумного access-лога


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.app = app
    return httpd


def serve_forever(app: App, host: str, port: int) -> None:
    httpd = make_server(app, host, port)
    print(f"Дашборд: http://{host}:{port}/  (обновление {app.interval}s, Ctrl+C для остановки)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
