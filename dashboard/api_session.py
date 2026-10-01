"""Non-secret capabilities and server-side token validation."""
import shutil
from .webapp import App


def capabilities(app: App) -> dict:
    r = app.runner
    return {
        "mode": "embedded" if r is not None else "standalone",
        "read_open": app.read_open, "auth_configured": bool(app.token),
        "edit_config": bool(app.token),
        "node_actions": bool(r is not None and r.storage is not None and app.token),
        "switch": bool(r is not None and r.switcher is not None and app.token),
        "run_pass": bool(r is not None and app.token),
        "pipeline": bool(r is not None and getattr(r, "orchestrator", None) is not None and app.token
                         and shutil.which("nodes-tester")),
        "singbox_files": bool(r is not None and getattr(r, "orchestrator", None) is not None and app.token),
        "singbox_control": bool(r is not None and getattr(r, "singbox", None) is not None
                                and r.singbox.available),
        "notify": bool(r is not None and getattr(r, "notifier", None) is not None and app.token),
        "quarantine_hours": app.cfg.cooldown.garbage_hours,
        "cooldown_enabled": app.cfg.cooldown.enabled,
    }


def register(app: App) -> None:
    @app.route("GET", "/api/capabilities")
    def get_capabilities(app, req):
        return capabilities(app)

    @app.route("GET", "/api/session", needs_token=True)
    def session(app, req):
        return {"authenticated": True, "capabilities": capabilities(app)}
