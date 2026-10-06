"""Share-ссылки и записи Clash → узлы sing-box (dict, tag — имя из подписки).

parse_link(line) — одна строка подписки; FOLDER_FORMATS — разбор каталогов файлов
(формат подписки type=folder). Ссылки неизвестных схем пропускаются.
"""

from __future__ import annotations

from . import anytls, awg, http, hysteria, hysteria2, socks, ss, trojan, tuic, vless, vmess, wireguard
from ._common import LinkError

SCHEMES = {
    "vless": vless.parse, "vmess": vmess.parse, "trojan": trojan.parse, "ss": ss.parse,
    "hysteria": hysteria.parse, "hysteria2": hysteria2.parse, "hy2": hysteria2.parse,
    "tuic": tuic.parse, "anytls": anytls.parse,
    "socks": socks.parse, "socks5": socks.parse, "http": http.parse, "https": http.parse,
    "wireguard": wireguard.parse, "wg": wireguard.parse,
}
FOLDER_FORMATS = {"awg": awg.parse_file}


def scheme_of(line: str) -> str:
    scheme, sep, _ = line.strip().partition("://")
    return scheme.lower() if sep else ""


def parse_link(line: str) -> list:
    """Узлы одной ссылки ([] — схема не поддерживается); LinkError/ValueError — битая ссылка."""
    parser = SCHEMES.get(scheme_of(line))
    if parser is None:
        return []
    node = parser(line.strip())
    if not node.get("tag"):
        node["tag"] = f"{node['type']} {node.get('server', '')}:{node.get('server_port', '')}"
    return [node]


__all__ = ["SCHEMES", "FOLDER_FORMATS", "LinkError", "parse_link", "scheme_of"]
