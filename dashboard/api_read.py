"""Read-эндпоинты админки (доступны в обоих режимах, без токена при read_open).

Вся выборка/агрегация — в [dashboard/data.py](dashboard/data.py); здесь только
маршрутизация. `/api/data` сохраняется как единый агрегат (обратная совместимость с
текущим фронтом), плюс узкие эндпоинты для SPA, которые отдают срезы того же снимка.
"""

from __future__ import annotations

from .data import collect
from .webapp import App, HttpError, Response

# Узкий эндпоинт → какие ключи снимка collect() он отдаёт.
_SLICES = {
    "rating": ("rating", "score_spark"),
    "history": ("history",),
    "results": ("results",),
    "traffic": ("traffic_providers", "traffic_countries", "traffic_protocols",
                "traffic_nodes", "endpoints"),
    "lifecycle": ("provider_quality", "garbage", "longevity", "dropouts", "attrition"),
    "graveyard": ("graveyard",),
}


def register(app: App) -> None:
    open_read = app.read_open

    def _snapshot(app: App) -> dict:
        try:
            return collect(app.cfg)
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
            return out
        return _h

    for name, keys in _SLICES.items():
        _make_slice(name, keys)
