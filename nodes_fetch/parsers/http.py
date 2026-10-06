"""http:// и https:// — HTTP-прокси (https — с TLS до прокси).

http(s)://user:pass@host:port#name; учётные данные или весь адрес бывают в base64.
Для https: sni=…, allowInsecure=1.
"""

from __future__ import annotations

from ._common import LinkError, is_true, split_link
from .socks import credentials, unwrap


def parse(text: str):
    link = unwrap(split_link(text))
    if not link.host:
        raise LinkError("нет адреса")
    node = {"tag": link.name, "type": "http",
            "server": link.host, "server_port": link.port_int()}
    if link.userinfo:
        node["username"], node["password"] = credentials(link)
    if link.scheme == "https":
        tls = {"enabled": True, "insecure": is_true(link.params.get("allowInsecure", ""))}
        if link.params.get("sni"):
            tls["server_name"] = link.params["sni"]
        node["tls"] = tls
    return node
