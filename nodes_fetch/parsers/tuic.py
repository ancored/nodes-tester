"""tuic:// — TUIC v5 в формате v2rayN/NekoBox.

tuic://uuid:password@host:port?congestion_control=bbr&udp_relay_mode=native&alpn=h3
       &sni=…&allow_insecure=1&disable_sni=1#name
"""

from __future__ import annotations

from ._common import LinkError, csv, is_true, none_like, split_link


def parse(text: str):
    link = split_link(text)
    p = link.params
    uuid, _, password = link.user.partition(":")
    if not link.host or not uuid:
        raise LinkError("нет адреса или uuid")
    node = {"tag": link.name,
            "type": "tuic",
            "server": link.host,
            "server_port": link.port_int(),
            "uuid": uuid,
            "password": password or p.get("password", "")}
    for key in ("congestion_control", "udp_relay_mode"):
        if p.get(key):
            node[key] = p[key]
    tls = {"enabled": True,
           "alpn": csv(p.get("alpn") or "h3"),
           "insecure": is_true(p.get("allow_insecure", "")) or is_true(p.get("insecure", ""))}
    sni = p.get("sni") or p.get("peer")
    if is_true(p.get("disable_sni", "")):
        tls["disable_sni"] = True
    elif not none_like(sni):
        tls["server_name"] = sni
    node["tls"] = tls
    return node
