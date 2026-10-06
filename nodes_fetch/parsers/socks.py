"""socks:// (socks5://) — SOCKS5-прокси.

socks://user:pass@host:port#name; учётные данные бывают в base64:
socks://base64(user:pass)@host:port, а у части клиентов base64 — весь адрес.
"""

from __future__ import annotations

from ._common import LinkError, b64text, split_link, to_port


def credentials(link):
    """user:pass из base64 или открытого вида."""
    try:
        decoded = b64text(link.userinfo)
    except LinkError:
        decoded = ""
    user, sep, password = (decoded if ":" in decoded else link.user).partition(":")
    return user, password


def unwrap(link):
    """Весь адрес в base64 (scheme://base64(user:pass@host:port)#name) → обычная ссылка."""
    if link.userinfo or link.port:
        return link
    inner = split_link(f"{link.scheme}://{b64text(link.host + link.path)}")
    inner.name = link.name
    return inner


def parse(text: str):
    link = unwrap(split_link(text))
    if not link.host:
        raise LinkError("нет адреса")
    node = {"tag": link.name, "type": "socks", "version": "5",
            "server": link.host, "server_port": to_port(link.port)}
    if link.userinfo:
        node["username"], node["password"] = credentials(link)
    return node
