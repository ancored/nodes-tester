"""Read-эндпоинты админки (доступны в обоих режимах, без токена при read_open).

Вся выборка/агрегация — в [dashboard/data.py](dashboard/data.py); здесь только
маршрутизация. `/api/data` — единый агрегат снимка, плюс узкие эндпоинты, которые отдают его срезы.
"""

from __future__ import annotations

from naming import NODE_KEY_RE

from .data import collect, node_detail
from .webapp import App, HttpError

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
        if not NODE_KEY_RE.fullmatch(crc.lower()):
            raise HttpError(400, "ключ ноды — node_crc.cred_crc, по 8 hex-символов")
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

    @app.route("GET", "/api/groups", needs_token=not open_read)
    def groups(app, req):
        """Группы из config.json и их живое состояние: активная нода или запасной выход,
        кандидаты (первые 5 по порядку выбора), резерв."""
        r = app.runner
        out = []
        for g in app.cfg.groups:
            item = {"tag": g.tag, "strict": g.strict, "direct_fallback": g.direct_fallback,
                    "in_global": g.in_global, "require": list(g.require), "prefer": list(g.prefer),
                    "live": r is not None}
            if r is not None:
                st = r.switcher.state.get(g.tag) or {}
                candidates = r.board.candidates(g.tag)
                fallback = None
                if not st.get("active") and st.get("failsafe"):
                    fallback = st.get("fallback") or r.switcher.fallback(g.tag)
                item.update(active=st.get("active"), fallback=fallback,
                            members=len(r.board.group_nodes(g.tag)), candidates=len(candidates),
                            top=[{"node": c["node"], "score": round(float(c.get("score") or 0), 1),
                                  "tier": c.get("tier", 0)} for c in candidates[:5]],
                            reserve=r.board.reserve(g.tag))
            out.append(item)
        return {"groups": out}

    @app.route("GET", "/api/nodes/{crc}/groups", needs_token=not open_read)
    def node_group_checks(app, req, crc):
        """Проверки групп для ноды: входит ли, кандидат ли, вердикты и время результатов."""
        if not NODE_KEY_RE.fullmatch(crc.lower()):
            raise HttpError(400, "ключ ноды — node_crc.cred_crc, по 8 hex-символов")
        r = app.runner
        node = r.board.node_by_key(crc.lower()) if r is not None else None
        if node is None:
            return {"live": r is not None, "groups": []}
        out = []
        for g in app.cfg.groups:
            out.append({"group": g.tag, "member": node in r.board.group_nodes(g.tag),
                        "candidate": any(c["node"] == node for c in r.board.candidates(g.tag)),
                        "active": r.switcher.active_node(g.tag) == node,
                        "reserve": node in r.board.reserve(g.tag),
                        "checks": r.board.check_report(node, g.tag)})
        return {"live": True, "groups": out}

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
