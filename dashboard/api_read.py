"""Read-эндпоинты админки (доступны в обоих режимах, без токена при read_open).

Вся выборка/агрегация — в [dashboard/data.py](dashboard/data.py); здесь только
маршрутизация. `/api/data` сохраняется как единый агрегат (обратная совместимость с
текущим фронтом), плюс узкие эндпоинты для SPA, которые отдают срезы того же снимка.
"""

from __future__ import annotations

from .data import collect, node_detail
from .webapp import App, HttpError, Response

# Узкий эндпоинт → какие ключи снимка collect() он отдаёт.
_SLICES = {
    "rating": ("rating", "score_spark"),
    "history": ("history",),
    "results": ("results",),
    "traffic": ("traffic_providers", "traffic_countries", "traffic_protocols",
                "traffic_nodes", "endpoints", "traffic_total", "traffic_range",
                "traffic_user_totals", "traffic_tester_totals", "traffic_tester_range",
                "traffic_test_download_24h"),
    "lifecycle": ("provider_quality", "garbage", "longevity", "dropouts", "attrition"),
    "graveyard": ("graveyard",),
}


def register(app: App) -> None:
    open_read = app.read_open

    def read_node(app, crc, include_config=False):
        import re
        if not re.fullmatch(r"[0-9a-fA-F]{8}", crc):
            raise HttpError(400, "crc должен состоять из 8 hex-символов")
        try:
            result = node_detail(app.cfg, crc.lower(), include_config)
        except Exception as exc:
            raise HttpError(500, f"Не удалось прочитать сведения ноды: {exc}") from exc
        if not result:
            raise HttpError(404, "Нода не найдена в базе")
        return result

    @app.route("GET", "/api/nodes/{crc}/score-history", needs_token=not open_read)
    def score_history(app, req, crc):
        return read_node(app, crc)

    @app.route("GET", "/api/nodes/{crc}/details", needs_token=True)
    def details(app, req, crc):
        return read_node(app, crc, include_config=True)

    def _snapshot(app: App) -> dict:
        try:
            data = collect(app.cfg)
            r = app.runner
            data["runner"] = r.status() if r is not None else None
            board = getattr(r, "board", None)
            candidates: dict[str, list[str]] = {}       # нода → группы, где она кандидат
            restricted = r.restricted_crcs() if r is not None else set()
            if board is not None and r.switcher is not None:
                for group in board.regions():
                    for c in board.candidates(group):
                        candidates.setdefault(c["node"], []).append(group)
            for n in data.get("nodes", []):
                allowed = (n.get("present") and not n.get("banned")
                           and n.get("crc") not in restricted)
                n["activate_groups"] = candidates.get(n.get("node"), []) if allowed else []
                n["can_activate"] = bool(n["activate_groups"])
            return data
        except Exception as exc:                    # noqa: BLE001
            raise HttpError(500, f"ошибка сбора данных: {exc}") from exc

    @app.route("GET", "/api/data", needs_token=not open_read)
    def api_data(app: App, req):                    # полный агрегат (как раньше)
        return _snapshot(app)

    def _make_slice(name: str, keys: tuple):
        @app.route("GET", f"/api/{name}", needs_token=not open_read)
        def _h(app: App, req, _keys=keys):
            snap = _snapshot(app)
            out = {k: snap.get(k) for k in _keys}
            out["generated"] = snap.get("generated")
            out["source"] = snap.get("source")
            return out
        return _h

    for name, keys in _SLICES.items():
        _make_slice(name, keys)
