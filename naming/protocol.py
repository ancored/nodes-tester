"""Дескриптор протокола ноды из её ВЫХОДНЫХ параметров (не из названия провайдера).

Формат: base|transport|masq, дефолты опускаются.
    base      — тип outbound, сокращённо (hysteria2->hy2, shadowsocks->ss, ...)
    transport — transport.type, если не tcp (ws/grpc/http/httpupgrade)
    masq      — reality | tls (маскировка); опускается, если её нет

Примеры:
    vless + reality (tcp)      -> vless|reality
    vless + grpc + reality     -> vless|grpc|reality
    vmess + ws + tls           -> vmess|ws|tls
    trojan + tls               -> trojan|tls
    hysteria2 (quic+tls нативно)-> hy2
    shadowsocks                -> ss

Единообразно для всех парсеров подписки: считается только из полей ноды.
"""

from __future__ import annotations

# QUIC-нативные протоколы: транспорт (quic) и tls неотъемлемы — не пишем.
_QUIC_NATIVE = {"hysteria2", "hysteria", "tuic"}
_SHORT = {
    "hysteria2": "hy2", "hysteria": "hy",
    "shadowsocks": "ss", "shadowsocksr": "ssr",
    "wireguard": "wg",
}


def has_reality(node: dict) -> bool:
    tls = node.get("tls")
    if not isinstance(tls, dict):
        return False
    reality = tls.get("reality")
    if isinstance(reality, dict):
        return bool(reality.get("enabled", True))
    return bool(reality)


def _tls_on(node: dict) -> bool:
    tls = node.get("tls")
    if isinstance(tls, dict):
        return bool(tls.get("enabled", True))
    return bool(tls)


def node_protocol(node: dict) -> str:
    typ = node.get("type", "")
    base = _SHORT.get(typ, typ)
    if typ in _QUIC_NATIVE:
        return base
    parts = [base]
    transport = node.get("transport")
    if isinstance(transport, dict):
        t = transport.get("type")
        if t and t != "tcp":
            parts.append(t)
    if has_reality(node):
        parts.append("reality")
    elif _tls_on(node):
        parts.append("tls")
    return "|".join(parts)
