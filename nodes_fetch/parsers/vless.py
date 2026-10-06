"""vless://uuid@host:port?security=…&type=…#name — формат Xray (XTLS/Xray-core#716).

Shadowrocket-вариант: vless://base64(auto:uuid@host:port)?…&remarks=имя.
"""

from __future__ import annotations

from ._common import (LinkError, add_reality, b64text, multiplex, none_like, split_hostport,
                      split_link, stream_transport, tls_block, ws_transport)


def _shadowrocket(link):
    """Адрес целиком в base64: 'auto:uuid@host:port'."""
    decoded = b64text(link.host + link.path)
    cred, _, hostport = decoded.rpartition("@")
    link.userinfo = cred.split(":", 1)[-1]
    link.host, link.port = split_hostport(hostport)
    return link


def parse(text: str):
    link = split_link(text)
    if not link.userinfo:
        link = _shadowrocket(link)
    p = link.params
    node = {"tag": p.get("remarks") or link.name,
            "type": "vless",
            "server": link.host,
            "server_port": link.port_int(),
            "uuid": link.user}
    if not node["server"] or not node["uuid"]:
        raise LinkError("нет адреса или uuid")
    # xudp — значение ядра по умолчанию; поле явно входит в отпечаток ноды.
    encoding = p.get("packetEncoding", "xudp")
    if not none_like(encoding):
        node["packet_encoding"] = encoding
    kind = (p.get("type") or "").lower()
    if not none_like(p.get("flow")) and kind != "xhttp":
        node["flow"] = p["flow"]

    security = (p.get("security") or "").lower()
    if not none_like(security) or p.get("tls") == "1" or p.get("pbk"):
        node["tls"] = tls_block(p)
        if security == "reality" or p.get("pbk"):
            add_reality(node["tls"], p)
            if p.get("fp"):
                node["tls"]["utls"]["fingerprint"] = p["fp"]

    if kind == "ws" or (not kind and p.get("obfs") == "websocket"):
        host = p.get("host") or p.get("obfsParam") or p.get("sni") or ""
        node["transport"] = ws_transport(p.get("path", "/"), host)
        tls = node.get("tls")
        if tls is not None and not tls["server_name"] and host:
            tls["server_name"] = host
    else:
        transport = stream_transport(p, server=link.host)
        if transport:
            node["transport"] = transport

    mux = multiplex(p)
    if mux:
        node["multiplex"] = mux
    if not none_like(p.get("encryption")):
        node["encryption"] = p["encryption"]
    return node
