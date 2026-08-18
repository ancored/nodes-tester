"""Фабрика HTTP-сессии, ходящей через SOCKS5 inbound sing-box."""

from __future__ import annotations

import requests

from .config import ConnectionConfig


def make_session(connection: ConnectionConfig) -> requests.Session:
    """requests.Session с проксированием всего трафика через SOCKS5.

    Каждой ноде — своя сессия, чтобы не переиспользовать TCP-соединения
    предыдущей (уже переключённой) ноды.
    """
    session = requests.Session()
    session.trust_env = False  # игнорировать системные HTTP(S)_PROXY
    proxy = connection.proxy_url
    session.proxies = {"http": proxy, "https": proxy}
    session.headers["User-Agent"] = "sing-box-nodes-tester/0.1"
    return session
