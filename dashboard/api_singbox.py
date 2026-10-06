"""Restricted JSON editor for package-managed sing-box source files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from nodes_admin import presets as presets_mod
from nodes_admin.merge import MergeError, dumps
from nodes_admin.rules import validate_rules

from .webapp import App, HttpError


_edit_lock = threading.Lock()
_name = r"[A-Za-z0-9_-]+"
_file = r"[A-Za-z0-9._-]+"
_allowed = re.compile(
    rf"(?:base\.json|rules\.json|rules/{_file}\.json|presets/{_name}\.json|"
    rf"clients/base_{_name}\.json|clients/publish/{_file})"
)


def _root(app: App) -> Path:
    config_path = getattr(app.cfg, "path", None)
    if not config_path:
        raise HttpError(409, "Путь config.json неизвестен")
    root = Path(config_path).resolve().parent / "singbox"
    if root.is_symlink():
        raise HttpError(400, "Символические ссылки для singbox не разрешены")
    return root


def _file_path(app: App, raw: str) -> tuple:
    name = unquote(raw)
    if ("\\" in name or "%" in name or not _allowed.fullmatch(name)
            or any(part in (".", "..") for part in name.split("/"))):
        raise HttpError(400, "Путь файла не разрешён")
    root = _root(app).resolve()
    path = root / name
    current = root
    for part in name.split("/"):
        current = current / part
        if current.is_symlink():
            raise HttpError(400, "Символические ссылки не разрешены")
    try:
        if os.path.commonpath((str(root), str(path.resolve()))) != str(root):
            raise HttpError(400, "Путь файла не разрешён")
    except ValueError as exc:
        raise HttpError(400, "Путь файла не разрешён") from exc
    return name, path


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _document(path: Path) -> dict:
    if not path.is_file():
        raise HttpError(404, "Файл ещё не создан")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HttpError(422, "Файл содержит невалидный JSON") from exc
    return {"data": data, "revision": hashlib.sha256(raw).hexdigest()}


def _load_presets(app: App, override: tuple | None = None) -> list:
    """Пресеты каталога; override = (имя, данные) подменяет/добавляет один файл."""
    try:
        items = presets_mod.load(_root(app) / "presets")
    except presets_mod.PresetError as exc:
        raise HttpError(422, str(exc)) from exc
    if override is not None:
        name, data = override
        try:
            meta = presets_mod.validate(name, data)
        except presets_mod.PresetError as exc:
            raise HttpError(422, str(exc)) from exc
        items = sorted([p for p in items if p["name"] != name] + [{"name": name, "meta": meta, "data": data}],
                       key=lambda p: p["name"])
    return items


def _check_assembly(app: App, what: str, base: dict | None = None,
                    override: tuple | None = None) -> None:
    """Склейка базы, nodes.json и включённых пресетов + sing-box check."""
    nodes_file = Path(app.cfg.storage.nodes_file)
    try:
        nodes = json.loads(nodes_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HttpError(422, f"Не найден nodes.json для проверки {what}") from exc
    except ValueError as exc:
        raise HttpError(422, f"nodes.json невалиден: {exc}") from exc
    if base is None:
        try:
            base = json.loads((_root(app) / "base.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HttpError(422, f"base.json недоступен для проверки {what}: {exc}") from exc
    try:
        config, _ = presets_mod.assemble(base, nodes, _load_presets(app, override))
    except MergeError as exc:
        raise HttpError(422, f"Итоговый конфиг не склеивается ({what}): {exc}") from exc
    binary = getattr(app, "singbox_binary", "sing-box")
    with tempfile.TemporaryDirectory() as work:
        merged = Path(work) / "config.json"
        merged.write_text(dumps(config), encoding="utf-8")
        try:
            result = subprocess.run([binary, "check", "-c", str(merged)],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HttpError(422, f"Не удалось проверить {what}: {exc}") from exc
        if result.returncode:
            output = (result.stderr or result.stdout).strip()[:4000]
            raise HttpError(422, f"sing-box отклонил итоговый конфиг ({what}): {output}")


def _validate(app: App, name: str, data: object) -> None:
    if not isinstance(data, dict):
        raise HttpError(422, "Корень JSON должен быть объектом")
    if name == "rules.json":
        try:
            validate_rules(data)
        except ValueError as exc:
            raise HttpError(422, str(exc)) from exc
    if name == "base.json":
        _check_assembly(app, "base.json", base=data)
    if name.startswith("presets/"):
        _check_assembly(app, name, override=(Path(name).stem, data))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".nt-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _history_dir(app: App, name: str) -> Path:
    root = _root(app)
    current = root / ".history"
    for part in name.split("/"):
        if current.is_symlink():
            raise HttpError(400, "Символические ссылки в истории не разрешены")
        current = current / part
    if current.is_symlink():
        raise HttpError(400, "Символические ссылки в истории не разрешены")
    return current


def _version(app: App, name: str, ts: str) -> Path:
    if not re.fullmatch(r"[0-9]{19}", ts):
        raise HttpError(400, "Недопустимая версия")
    version = _history_dir(app, name) / ts
    if version.is_symlink() or not version.is_file():
        raise HttpError(404, "Версия не найдена")
    return version


def _snapshot(app: App, name: str, path: Path) -> Path:
    """Сохранить текущую версию файла в историю; возвращает каталог истории."""
    history = _history_dir(app, name)
    history.mkdir(parents=True, exist_ok=True)
    _atomic_write(history / str(time.time_ns()), path.read_bytes())
    return history


def _save(app: App, req, name: str, path: Path, data: dict) -> dict:
    expected = req.headers.get("If-Match")
    if not expected:
        raise HttpError(400, "Нужен If-Match с revision файла")
    _validate(app, name, data)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with _edit_lock:
        if _revision(path) != expected:
            raise HttpError(409, "Файл изменился после открытия. Перечитайте его.")
        if path.is_file():
            history = _snapshot(app, name, path)
            for stale in sorted(history.iterdir(), key=lambda p: p.name, reverse=True)[20:]:
                stale.unlink()
        _atomic_write(path, payload)
    return {"ok": True, "revision": _revision(path)}


def register(app: App) -> None:
    @app.route("GET", "/api/singbox/files", needs_token=True)
    def files(app, req):
        root = _root(app)
        found = []
        for path in root.rglob("*") if root.is_dir() else []:
            if path.is_file() and ".history" not in path.parts:
                relative = path.relative_to(root).as_posix()
                try:
                    _file_path(app, relative)
                except HttpError:
                    continue
                found.append({"path": relative, "revision": _revision(path)})
        return {"files": sorted(found, key=lambda item: item["path"])}

    @app.route("GET", "/api/singbox/presets", needs_token=True)
    def list_presets(app, req):
        try:
            items = presets_mod.load(_root(app) / "presets")
        except presets_mod.PresetError as exc:
            return {"presets": [], "error": str(exc)}
        nodes_file = Path(app.cfg.storage.nodes_file)
        groups = presets_mod.node_groups(nodes_file) if nodes_file.is_file() else None
        result = presets_mod.status(items, groups)
        for item in result:
            item["path"] = f"presets/{item['name']}.json"
            item["revision"] = _revision(_root(app) / item["path"])
        return {"presets": result, "groups": sorted(groups) if groups is not None else None}

    @app.route("DELETE", "/api/singbox/files/{path...}", needs_token=True)
    def delete_file(app, req, path):
        name, target = _file_path(app, path)
        if not name.startswith("presets/"):
            raise HttpError(400, "Удалять можно только пресеты")
        expected = req.headers.get("If-Match")
        if not expected:
            raise HttpError(400, "Нужен If-Match с revision файла")
        with _edit_lock:
            if _revision(target) != expected:
                raise HttpError(409, "Файл изменился после открытия. Перечитайте его.")
            if target.is_file():
                _snapshot(app, name, target)
                target.unlink()
        return {"ok": True}

    @app.route("GET", "/api/singbox/files/{path...}/history", needs_token=True)
    def history(app, req, path):
        name, _ = _file_path(app, path)
        ts = req.q("ts")
        if ts is not None:
            return _document(_version(app, name, ts))
        folder = _history_dir(app, name)
        return {"versions": sorted((item.name for item in folder.iterdir() if item.is_file()), reverse=True)
                if folder.is_dir() else []}

    @app.route("POST", "/api/singbox/files/{path...}/restore", needs_token=True)
    def restore(app, req, path):
        name, target = _file_path(app, path)
        body = req.json()
        if not isinstance(body, dict) or set(body) != {"ts"} or not isinstance(body["ts"], str):
            raise HttpError(400, "Нужен ts версии")
        version = _version(app, name, body["ts"])
        try:
            data = json.loads(version.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise HttpError(422, "Сохранённая версия повреждена") from exc
        return _save(app, req, name, target, data)

    @app.route("GET", "/api/singbox/files/{path...}", needs_token=True)
    def get_file(app, req, path):
        _name, target = _file_path(app, path)
        return _document(target)

    @app.route("PUT", "/api/singbox/files/{path...}", needs_token=True)
    def put_file(app, req, path):
        name, target = _file_path(app, path)
        return _save(app, req, name, target, req.json())
