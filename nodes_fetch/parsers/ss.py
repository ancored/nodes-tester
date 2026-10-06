"""ss:// — Shadowsocks: SIP002 и прежний формат.

SIP002:  ss://base64url(method:password)@host:port/?plugin=name;opt=v;…#name
         (для 2022-blake3 userinfo бывает %-кодированным method:password без base64)
Прежний: ss://base64(method:password@host:port)#name
Опции: uot=1 / udp-over-tcp=true — UDP поверх TCP; protocol=smux|yamux|h2mux — мультиплекс.
"""

from __future__ import annotations

from urllib.parse import unquote

from ._common import (LinkError, b64text, is_true, multiplex, parse_query, split_hostport,
                      split_link, to_port)

# Старые имена AEAD-шифров → имена sing-box.
METHOD_ALIASES = {"chacha20-poly1305": "chacha20-ietf-poly1305",
                  "xchacha20-poly1305": "xchacha20-ietf-poly1305"}
_PLUGIN_ALIASES = {"simple-obfs": "obfs-local", "obfs": "obfs-local"}


def _credentials(userinfo: str):
    try:
        decoded = b64text(userinfo)
    except LinkError:
        decoded = unquote(userinfo)
    method, sep, password = decoded.partition(":")
    if not sep or not method:
        raise LinkError("нет method:password")
    return method, password


def _legacy(text: str):
    """ss://base64(method:password@host:port)[?…]#name → Link-подобный набор."""
    body, _, name = text[len("ss://"):].partition("#")
    body, _, query = body.partition("?")
    decoded = b64text(body.rstrip("/"))
    cred, at, hostport = decoded.rpartition("@")
    if not at:
        raise LinkError("ss: нет адреса")
    host, port = split_hostport(hostport)
    return cred, host, port, parse_query(query), unquote(name)


def parse(text: str):
    text = text.strip()
    link = split_link(text)
    if link.userinfo:
        method, password = _credentials(link.userinfo)
        host, port, params, name = link.host, link.port, link.params, link.name
    else:
        cred, host, port, params, name = _legacy(text)
        method, _, password = cred.partition(":")
    if not host or not method:
        raise LinkError("ss: нет адреса или шифра")
    node = {"tag": name or params.get("remarks", ""),
            "type": "shadowsocks",
            "server": host,
            "server_port": to_port(port),
            "method": METHOD_ALIASES.get(method.lower(), method),
            "password": password}
    plugin = params.get("plugin")
    if plugin:
        plugin_name, _, opts = plugin.partition(";")
        node["plugin"] = _PLUGIN_ALIASES.get(plugin_name, plugin_name)
        if opts:
            node["plugin_opts"] = opts
    if is_true(params.get("uot", "")) or is_true(params.get("udp-over-tcp", "")):
        node["udp_over_tcp"] = {"enabled": True, "version": 2}
    mux = multiplex(params)
    if mux:
        node["multiplex"] = mux
    return node
