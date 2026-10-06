"""hysteria:// — Hysteria 1 (v1.hysteria.network/docs/uri-scheme).

hysteria://host:port?protocol=udp&auth=…&peer=…&insecure=1&upmbps=…&downmbps=…
          &alpn=…&obfs=xplus&obfsParam=…#name
upmbps/downmbps обязательны в протоколе; без них — 10/50 Мбит/с.
"""

from __future__ import annotations

import re

from ._common import LinkError, csv, is_true, none_like, split_link


def _mbps(value, default):
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else default


def parse(text: str):
    link = split_link(text)
    p = link.params
    if not link.host:
        raise LinkError("нет адреса")
    node = {"tag": link.name,
            "type": "hysteria",
            "server": link.host,
            "server_port": link.port_int(),
            "up_mbps": _mbps(p.get("upmbps"), 10),
            "down_mbps": _mbps(p.get("downmbps"), 50)}
    if p.get("auth"):
        node["auth_str"] = p["auth"]
    if p.get("obfsParam"):
        node["obfs"] = p["obfsParam"]
    tls = {"enabled": True,
           "insecure": is_true(p.get("insecure", "")) or is_true(p.get("allowInsecure", ""))}
    sni = p.get("peer") or p.get("sni")
    if not none_like(sni):
        tls["server_name"] = sni
    if not none_like(p.get("alpn")):
        tls["alpn"] = csv(p["alpn"])
    node["tls"] = tls
    return node
