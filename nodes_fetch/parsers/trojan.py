"""trojan://password@host:port?sni=…&type=…#name — формат trojan-go / Xray.

TLS у trojan обязателен; транспорт — как у vless (ws, grpc, h2, httpupgrade, xhttp).
"""

from __future__ import annotations

from ._common import LinkError, multiplex, split_link, stream_transport, tls_block


def parse(text: str):
    link = split_link(text)
    p = link.params
    if not link.userinfo or not link.host:
        raise LinkError("нет пароля или адреса")
    node = {"tag": link.name,
            "type": "trojan",
            "server": link.host,
            "server_port": link.port_int(),
            "password": link.user,
            "tls": tls_block(p)}
    if not node["tls"]["server_name"]:
        del node["tls"]["server_name"]
    if (p.get("type") or "").lower() in ("h2", "http"):
        p = dict(p, host=p.get("host") or link.host, path=p.get("path") or "/")
    transport = stream_transport(p, server=link.host)
    if transport:
        if transport["type"] == "ws":
            transport.setdefault("headers", {})   # пустой объект — часть отпечатка ноды
        node["transport"] = transport
    mux = multiplex(p)
    if mux:
        node["multiplex"] = mux
    return node
