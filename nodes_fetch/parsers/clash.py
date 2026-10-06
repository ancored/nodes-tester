"""Clash / mihomo: элемент списка proxies → узел sing-box.

Поля — по документации mihomo (wiki.metacubex.one, раздел proxies). Неподдерживаемые
типы (ssr, snell и др.) дают None — подписка их пропускает.
"""

from __future__ import annotations

import re

_SS_ALIASES = {"chacha20-poly1305": "chacha20-ietf-poly1305",
               "xchacha20-poly1305": "xchacha20-ietf-poly1305"}


def _tls(proxy, *, always=False, sni_key="servername"):
    if not (always or proxy.get("tls") or proxy.get("reality-opts")):
        return None
    tls = {"enabled": True, "insecure": proxy.get("skip-cert-verify") is True}
    sni = proxy.get(sni_key) or proxy.get("sni") or proxy.get("servername")
    if sni:
        tls["server_name"] = sni
    if proxy.get("alpn"):
        tls["alpn"] = list(proxy["alpn"])
    if proxy.get("client-fingerprint"):
        tls["utls"] = {"enabled": True, "fingerprint": proxy["client-fingerprint"]}
    reality = proxy.get("reality-opts")
    if reality:
        tls["reality"] = {"enabled": True, "public_key": reality.get("public-key")}
        if reality.get("short-id"):
            tls["reality"]["short_id"] = reality["short-id"]
        tls.setdefault("utls", {"enabled": True})
    return tls


def _transport(proxy):
    network = proxy.get("network", "tcp")
    if network == "ws":
        opts = proxy.get("ws-opts") or {}
        transport = {"type": "ws", "path": opts.get("path") or proxy.get("ws-path") or "/"}
        host = (opts.get("headers") or proxy.get("ws-headers") or {}).get("Host")
        if host:
            transport["headers"] = {"Host": host}
        if opts.get("max-early-data"):
            transport["max_early_data"] = int(opts["max-early-data"])
            transport["early_data_header_name"] = opts.get("early-data-header-name",
                                                           "Sec-WebSocket-Protocol")
        if opts.get("v2ray-http-upgrade"):
            transport = {"type": "httpupgrade", "path": transport["path"]}
            if host:
                transport["host"] = host
        return transport
    if network == "grpc":
        name = (proxy.get("grpc-opts") or {}).get("grpc-service-name", "")
        return {"type": "grpc", "service_name": "" if name == "/" else name}
    if network == "h2":
        opts = proxy.get("h2-opts") or {}
        transport = {"type": "http"}
        if opts.get("host"):
            transport["host"] = list(opts["host"])
        if opts.get("path"):
            transport["path"] = opts["path"]
        return transport
    if network == "http":
        opts = proxy.get("http-opts") or {}
        transport = {"type": "http"}
        host = (opts.get("headers") or {}).get("Host")
        if host:
            transport["host"] = list(host) if isinstance(host, list) else [host]
        path = opts.get("path")
        if path:
            transport["path"] = path[0] if isinstance(path, list) else path
        if opts.get("method"):
            transport["method"] = opts["method"]
        return transport
    return None


def _multiplex(proxy):
    smux = proxy.get("smux") or {}
    if smux.get("enabled") is not True:
        return None
    mux = {"enabled": True, "protocol": smux.get("protocol", "h2mux")}
    for src, dst in (("max-connections", "max_connections"), ("min-streams", "min_streams"),
                     ("max-streams", "max_streams")):
        if isinstance(smux.get(src), int):
            mux[dst] = smux[src]
    if smux.get("padding") is True:
        mux["padding"] = True
    return mux


def _mbps(value):
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else None


def _ports(value):
    """mihomo ports: '443,1000-2000' → ['1000:2000', '443:443']."""
    out = []
    for part in str(value or "").split(","):
        part = part.strip()
        if re.fullmatch(r"\d+(-\d+)?", part):
            lo, _, hi = part.partition("-")
            out.append(f"{lo}:{hi or lo}")
    return out


def _vmess(p, node):
    node.update(uuid=p["uuid"], security=p.get("cipher", "auto"),
                alter_id=int(p.get("alterId", 0)), packet_encoding="xudp")


def _vless(p, node):
    node.update(uuid=p["uuid"])
    encoding = p.get("packet-encoding") or "xudp"
    if encoding != "none":
        node["packet_encoding"] = encoding
    if p.get("flow"):
        node["flow"] = p["flow"]
    if p.get("encryption") and p["encryption"] != "none":
        node["encryption"] = p["encryption"]


def _shadowsocks(p, node):
    node.update(method=_SS_ALIASES.get(p["cipher"], p["cipher"]), password=p["password"])
    plugin, opts = p.get("plugin"), p.get("plugin-opts") or {}
    if plugin in ("obfs", "obfs-local"):
        node["plugin"] = "obfs-local"
        node["plugin_opts"] = ";".join(f"{k}={v}" for k, v in (("obfs", opts.get("mode")),
                                                               ("obfs-host", opts.get("host"))) if v)
    elif plugin == "v2ray-plugin":
        parts = [f"mode={opts.get('mode', 'websocket')}"]
        parts += [f"{k}={opts[k]}" for k in ("host", "path") if opts.get(k)]
        parts += [flag for flag in ("tls", "mux") if opts.get(flag) is True]
        node["plugin"] = "v2ray-plugin"
        node["plugin_opts"] = ";".join(parts)
    if p.get("udp-over-tcp") is True:
        node["udp_over_tcp"] = {"enabled": True, "version": int(p.get("udp-over-tcp-version", 2))}


def _shadow_tls(p, node):
    """ss + plugin shadow-tls → два узла: shadowtls и shadowsocks через него."""
    opts = p.get("plugin-opts") or {}
    tls_node = {"tag": node["tag"] + "_shadowtls", "type": "shadowtls",
                "server": node.pop("server"), "server_port": node.pop("server_port"),
                "version": int(opts.get("version", 2)), "password": opts.get("password", ""),
                "tls": {"enabled": True, "server_name": opts.get("host", "")}}
    if p.get("client-fingerprint"):
        tls_node["tls"]["utls"] = {"enabled": True, "fingerprint": p["client-fingerprint"]}
    node["detour"] = tls_node["tag"]
    return [node, tls_node]


def _hysteria(p, node):
    node.update(up_mbps=_mbps(p.get("up")) or 10, down_mbps=_mbps(p.get("down")) or 50)
    if p.get("auth-str") or p.get("auth_str"):
        node["auth_str"] = p.get("auth-str") or p["auth_str"]
    if p.get("obfs"):
        node["obfs"] = p["obfs"]
    if p.get("ports"):
        node["server_ports"] = _ports(p["ports"])


def _hysteria2(p, node):
    node["password"] = p.get("password") or p.get("auth", "")
    if p.get("ports"):
        node["server_ports"] = _ports(p["ports"])
    for key, field in (("up", "up_mbps"), ("down", "down_mbps")):
        if _mbps(p.get(key)):
            node[field] = _mbps(p[key])
    if p.get("obfs"):
        node["obfs"] = {"type": p["obfs"], "password": p.get("obfs-password", "")}


def _tuic(p, node):
    node.update(uuid=p["uuid"], password=p.get("password", ""))
    if p.get("congestion-controller"):
        node["congestion_control"] = p["congestion-controller"]
    if p.get("udp-relay-mode"):
        node["udp_relay_mode"] = p["udp-relay-mode"]
    if p.get("disable-sni") is True:
        node["tls"]["disable_sni"] = True
    node["tls"].setdefault("alpn", ["h3"])


def _anytls(p, node):
    node["password"] = p["password"]
    for src, dst in (("idle-session-check-interval", "idle_session_check_interval"),
                     ("idle-session-timeout", "idle_session_timeout")):
        if p.get(src):
            node[dst] = f"{p[src]}s"
    if p.get("min-idle-session") is not None:
        node["min_idle_session"] = int(p["min-idle-session"])


def _wireguard(p, node):
    peer = {"address": node.pop("server"), "port": node.pop("server_port"),
            "public_key": p["public-key"], "allowed_ips": list(p.get("allowed-ips") or ["0.0.0.0/0"]),
            "persistent_keepalive_interval": 30}
    if p.get("pre-shared-key"):
        peer["pre_shared_key"] = p["pre-shared-key"]
    reserved = p.get("reserved")
    if isinstance(reserved, str):
        reserved = [int(x) for x in reserved.split(",") if x.strip()]
    if reserved:
        peer["reserved"] = list(reserved)
    address = [a if "/" in a else f"{a}/32" for a in [p.get("ip")] if a]
    address += [a if "/" in a else f"{a}/128" for a in [p.get("ipv6")] if a]
    node.update(private_key=p["private-key"], address=address, peers=[peer])
    if p.get("mtu"):
        node["mtu"] = int(p["mtu"])


def _credentials(p, node):
    if p.get("username"):
        node["username"] = str(p["username"])
        node["password"] = str(p.get("password", ""))


def _socks(p, node):
    node["version"] = "5"
    _credentials(p, node)


# type: (тип sing-box, заполнение, TLS: None — нет, "opt" — по полю tls, "always")
_TYPES = {
    "vmess": ("vmess", _vmess, "opt"),
    "vless": ("vless", _vless, "opt"),
    "trojan": ("trojan", lambda p, node: node.update(password=p["password"]), "always"),
    "ss": ("shadowsocks", _shadowsocks, None),
    "hysteria": ("hysteria", _hysteria, "always"),
    "hysteria2": ("hysteria2", _hysteria2, "always"),
    "tuic": ("tuic", _tuic, "always"),
    "anytls": ("anytls", _anytls, "always"),
    "wireguard": ("wireguard", _wireguard, None),
    "http": ("http", _credentials, "opt"),
    "socks5": ("socks", _socks, None),
}


def convert(proxy: dict):
    """Узел (или список из двух для shadow-tls); None — тип не поддерживается."""
    spec = _TYPES.get(proxy.get("type"))
    if spec is None:
        return None
    kind, fill, tls_mode = spec
    node = {"tag": str(proxy.get("name", "")), "type": kind,
            "server": proxy["server"], "server_port": int(proxy["port"])}
    if tls_mode:
        tls = _tls(proxy, always=tls_mode == "always",
                   sni_key="servername" if kind in ("vmess", "vless") else "sni")
        if tls:
            node["tls"] = tls
    fill(proxy, node)
    if kind in ("vmess", "vless", "trojan"):
        transport = _transport(proxy)
        if transport:
            node["transport"] = transport
            # Как в mihomo: без servername SNI берётся из Host заголовка ws
            host = (transport.get("headers") or {}).get("Host")
            if host and "tls" in node and not node["tls"].get("server_name"):
                node["tls"]["server_name"] = host
    mux = _multiplex(proxy)
    if mux:
        node["multiplex"] = mux
    if kind == "shadowsocks" and proxy.get("plugin") == "shadow-tls":
        return _shadow_tls(proxy, node)
    return node
