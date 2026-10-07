"""JSON-подписка Xray: массив полных конфигов клиента (Remnawave, Marzban и др.) → узлы sing-box.

Панели отдают её клиентам на Xray (v2rayNG, Happ): по конфигу на пункт меню, имя — remarks.
Обычно в конфиге один outbound-прокси; у «Авто» и пунктов с резервом их несколько и они
повторяют ноды из других пунктов. Повтор пропускается; имя ноды — самого узкого пункта, где
она встретилась.

Поля — по документации Xray (xtls.github.io, раздел outbounds и transport). Mux Xray
(mux.cool) в sing-box не переносится. Неподдерживаемые протоколы пропускаются.
"""

from __future__ import annotations

import json
from collections import Counter

from ._common import LinkError, add_reality, none_like, ws_transport, xhttp_transport
from .ss import METHOD_ALIASES

_SKIP_PROTOCOLS = {"freedom", "blackhole", "dns", "loopback"}


def configs(text: str):
    """Список конфигов Xray, если text — JSON Xray (массив или один конфиг); иначе None."""
    if not text.startswith(("[", "{")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(c, dict) and isinstance(c.get("outbounds"), list)
                            for c in items):
        return None
    # У sing-box outbound — "type", у Xray — "protocol".
    if not any("protocol" in o for c in items for o in c["outbounds"] if isinstance(o, dict)):
        return None
    return items


def _tls(stream, server):
    security = (stream.get("security") or "").lower()
    if security not in ("tls", "reality"):
        return None
    opts = stream.get(f"{security}Settings") or {}
    sni = opts.get("serverName") or ("" if security == "reality" else server)
    tls = {"enabled": True, "insecure": opts.get("allowInsecure") is True,
           "server_name": "" if none_like(sni) else sni}
    if opts.get("alpn"):
        tls["alpn"] = list(opts["alpn"])
    if opts.get("fingerprint"):
        tls["utls"] = {"enabled": True, "fingerprint": opts["fingerprint"]}
    if security == "reality":
        add_reality(tls, {"pbk": opts.get("publicKey"), "sid": opts.get("shortId")})
    return tls


def _transport(stream):
    network = (stream.get("network") or "tcp").lower()
    if network == "ws":
        opts = stream.get("wsSettings") or {}
        host = opts.get("host") or (opts.get("headers") or {}).get("Host", "")
        return ws_transport(opts.get("path", "/"), host)
    if network == "grpc":
        return {"type": "grpc",
                "service_name": (stream.get("grpcSettings") or {}).get("serviceName", "")}
    if network == "httpupgrade":
        opts = stream.get("httpupgradeSettings") or {}
        transport = {"type": "httpupgrade", "path": opts.get("path") or "/"}
        if opts.get("host"):
            transport["host"] = opts["host"]
        return transport
    if network in ("xhttp", "splithttp"):
        opts = dict(stream.get("xhttpSettings") or stream.get("splithttpSettings") or {})
        extra = opts.pop("extra", None)
        opts.update(extra if isinstance(extra, dict) else {})
        # В JSON Xray числа (uplinkChunkSize: 0) — числа, а sing-box-lx ждёт в этих полях
        # строки; 0 у Xray значит «по умолчанию», такие поля не переносим.
        for key, value in list(opts.items()):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value:
                    opts[key] = str(int(value))
                else:
                    del opts[key]
        return xhttp_transport(opts)
    if network in ("tcp", "raw"):
        header = ((stream.get("tcpSettings") or stream.get("rawSettings") or {})
                  .get("header") or {})
        if header.get("type") == "http":
            raise LinkError("tcp с HTTP-маскировкой не поддерживается")
        return None
    raise LinkError(f"транспорт {network!r} не поддерживается")


def _server(settings):
    """Первый сервер: vnext[0] (vless, vmess) или servers[0] (trojan, ss); иначе settings."""
    for key in ("vnext", "servers"):
        if settings.get(key):
            return settings[key][0]
    return settings


def _vless(server, node):
    user = (server.get("users") or [{}])[0]
    node["uuid"] = user.get("id", "")
    node["packet_encoding"] = "xudp"
    if not none_like(user.get("flow")):
        node["flow"] = user["flow"]
    if not none_like(user.get("encryption")):
        node["encryption"] = user["encryption"]


def _vmess(server, node):
    user = (server.get("users") or [{}])[0]
    node.update(uuid=user.get("id", ""), security=user.get("security") or "auto",
                alter_id=int(user.get("alterId", 0)), packet_encoding="xudp")


def _trojan(server, node):
    node["password"] = server.get("password", "")


def _shadowsocks(server, node):
    method = server.get("method", "")
    node.update(method=METHOD_ALIASES.get(method.lower(), method),
                password=server.get("password", ""))
    if server.get("uot") is True:
        node["udp_over_tcp"] = {"enabled": True, "version": int(server.get("UoTVersion", 2))}


# protocol Xray: (type sing-box, заполнение, есть ли транспорт v2ray)
_TYPES = {
    "vless": ("vless", _vless, True),
    "vmess": ("vmess", _vmess, True),
    "trojan": ("trojan", _trojan, True),
    "shadowsocks": ("shadowsocks", _shadowsocks, False),
}


def _hysteria(outbound, stream):
    """Xray hysteria (version 2) → hysteria2 sing-box."""
    settings = outbound.get("settings") or {}
    opts = stream.get("hysteriaSettings") or {}
    if int(settings.get("version") or opts.get("version") or 2) != 2:
        raise LinkError("hysteria v1 не поддерживается")
    tls = _tls(stream, settings.get("address", "")) or {"enabled": True}
    tls.pop("utls", None)
    tls.setdefault("alpn", ["h3"])
    return {"type": "hysteria2", "server": settings.get("address"),
            "server_port": int(settings.get("port", 443)),
            "password": opts.get("auth", ""), "tls": tls}


def convert(outbound: dict):
    """outbound Xray → узел sing-box без тега; None — не прокси или протокол не поддерживается."""
    protocol = (outbound.get("protocol") or "").lower()
    stream = outbound.get("streamSettings") or {}
    if protocol == "hysteria":
        return _hysteria(outbound, stream)
    spec = _TYPES.get(protocol)
    if spec is None:
        return None
    kind, fill, has_transport = spec
    server = _server(outbound.get("settings") or {})
    node = {"type": kind, "server": server.get("address"),
            "server_port": int(server.get("port", 0))}
    if not node["server"] or not node["server_port"]:
        raise LinkError("нет адреса или порта")
    fill(server, node)
    tls = _tls(stream, node["server"])
    if tls:
        node["tls"] = tls
    if has_transport:
        transport = _transport(stream)
        if transport:
            node["transport"] = transport
            if transport["type"] == "xhttp":
                node.pop("flow", None)                # как в vless.parse: flow у xhttp не задаётся
            host = (transport.get("headers") or {}).get("Host")
            if host and tls and not tls["server_name"]:
                tls["server_name"] = host
    return node


def _identity(node):
    """Ключ повтора: содержимое без short_id Reality — панели выдают его случайно из
    нескольких допустимых, и одна нода в разных пунктах приходит с разными short_id."""
    reality = (node.get("tls") or {}).get("reality")
    if reality and "short_id" in reality:
        node = {**node, "tls": {**node["tls"], "reality": {k: v for k, v in reality.items()
                                                           if k != "short_id"}}}
    return json.dumps(node, sort_keys=True)


def _proxies(config, log):
    """Узлы-прокси одного конфига без тегов; битые outbound-ы пропускаются с записью в лог."""
    name = config.get("remarks") or ""
    found = []
    for outbound in config["outbounds"]:
        if not isinstance(outbound, dict) or outbound.get("protocol") in _SKIP_PROTOCOLS:
            continue
        try:
            node = convert(outbound)
        except (LinkError, KeyError, ValueError, TypeError) as exc:
            log(f"  [fetch] пропущен outbound Xray {outbound.get('protocol')!r} в «{name}» "
                f"({exc.__class__.__name__}: {exc})")
            continue
        if node is not None:
            found.append(node)
    return found


def nodes(items, log):
    """Конфиги Xray → узлы с тегом из remarks; повторы пропускаются.

    Имя ноды — самого узкого пункта, где она есть (меньше всего нод; при равенстве — первого):
    «Финляндия-1», а не «Авто». Нескольким нодам с одним именем добавляется номер."""
    parsed = [(str(c.get("remarks") or ""), _proxies(c, log)) for c in items]
    unique = {}                                     # ключ ноды → первое вхождение
    best = {}                                       # ключ ноды → ((размер пункта, номер), имя)
    for index, (name, found) in enumerate(parsed):
        for node in found:
            key = _identity(node)
            unique.setdefault(key, node)
            rank = (len(found), index)
            if key not in best or rank < best[key][0]:
                best[key] = (rank, name)
    per_name = Counter(best[key][1] for key in unique)
    numbers = Counter()
    result = []
    for key, node in unique.items():
        name = best[key][1]
        if not name:
            tag = f"{node['type']} {node['server']}:{node['server_port']}"
        elif per_name[name] == 1:
            tag = name
        else:
            numbers[name] += 1
            tag = f"{name} {numbers[name]}"
        result.append({"tag": tag, **node})
    return result
