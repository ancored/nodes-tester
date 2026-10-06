"""anytls://password@host:port/?sni=…&insecure=1#name — формат anytls-go.

Дополнительно: fp, alpn и параметры пула сессий idleSessionCheckInterval,
idleSessionTimeout (секунды), minIdleSession.
"""

from __future__ import annotations

from ._common import LinkError, split_link, tls_block


def parse(text: str):
    link = split_link(text)
    p = link.params
    if not link.host:
        raise LinkError("нет адреса")
    node = {"tag": link.name,
            "type": "anytls",
            "server": link.host,
            "server_port": link.port_int(),
            "password": p.get("auth") or link.user,
            "tls": tls_block(p)}
    if not node["tls"]["server_name"]:
        del node["tls"]["server_name"]
    for key, field in (("idleSessionCheckInterval", "idle_session_check_interval"),
                       ("idleSessionTimeout", "idle_session_timeout")):
        if p.get(key, "").isdigit():
            node[field] = p[key] + "s"
    if p.get("minIdleSession", "").isdigit():
        node["min_idle_session"] = int(p["minIdleSession"])
    return node
