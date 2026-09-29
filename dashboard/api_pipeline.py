"""Token-protected, allowlisted pipeline control API."""

from __future__ import annotations

from nodes_admin.orchestrator import BusyError

from .webapp import App, HttpError, Response


def _orchestrator(app):
    orchestrator = getattr(app.runner, "orchestrator", None)
    if orchestrator is None:
        raise HttpError(409, "Конвейер недоступен: включите токен и pipeline.json в тестере")
    return orchestrator


def register(app: App) -> None:
    # Logs may contain subscription URLs and other operational secrets.
    read_auth = True

    @app.route("GET", "/api/pipeline", needs_token=read_auth)
    def status(app, req):
        return _orchestrator(app).status()

    @app.route("POST", "/api/pipeline/run", needs_token=True)
    def run(app, req):
        data = req.json()
        if not isinstance(data, dict) or set(data) != {"mode", "dry_run"}:
            raise HttpError(400, "Нужны только mode и dry_run")
        try:
            run_id = _orchestrator(app).start_run(data["mode"], data["dry_run"])
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        except BusyError as exc:
            raise HttpError(409, str(exc)) from exc
        return Response.json({"run_id": run_id}, 202)

    @app.route("GET", "/api/pipeline/runs", needs_token=read_auth)
    def runs(app, req):
        try:
            limit = int(req.q("limit", "20"))
        except ValueError as exc:
            raise HttpError(400, "limit должен быть числом") from exc
        return {"runs": _orchestrator(app).runs(limit)}

    @app.route("GET", "/api/pipeline/runs/{run_id}/log", needs_token=read_auth)
    def log(app, req, run_id):
        try:
            offset = int(req.q("offset", "0"))
            if offset < 0:
                raise ValueError
        except ValueError as exc:
            raise HttpError(400, "offset должен быть неотрицательным числом") from exc
        try:
            return _orchestrator(app).log(run_id, offset)
        except KeyError as exc:
            raise HttpError(404, "Прогон не найден") from exc

    @app.route("GET", "/api/pipeline/schedule", needs_token=read_auth)
    def get_schedule(app, req):
        data, revision = _orchestrator(app).read_schedule()
        return {"data": data, "revision": revision}

    @app.route("PUT", "/api/pipeline/schedule", needs_token=True)
    def put_schedule(app, req):
        expected = req.headers.get("If-Match")
        if not expected:
            raise HttpError(400, "Нужен If-Match с revision прочитанного расписания")
        try:
            revision = _orchestrator(app).write_schedule(req.json(), expected)
        except ValueError as exc:
            if str(exc) == "revision_conflict":
                raise HttpError(409, "Расписание изменилось. Перечитайте его.") from exc
            raise HttpError(400, str(exc)) from exc
        return {"ok": True, "revision": revision}
