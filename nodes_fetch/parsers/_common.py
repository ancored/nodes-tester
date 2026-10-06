"""Общая часть разбора share-ссылок: адрес, параметры, TLS, транспорты, мультиплекс.

Ссылки вида scheme://userinfo@host:port/path?query#name разбираются вручную, а не через
urllib.parse.urlparse: в паролях встречаются '[', ']' и '@', а urlparse принимает их за
IPv6-адрес или ломается. В query '+' — литерал (base64-ключи), а не пробел.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from urllib.parse import unquote


class LinkError(ValueError):
    """Ссылка не разбирается — строка подписки пропускается."""


def b64decode(text: str) -> bytes:
    """Base64 обоих алфавитов, с паддингом и без, с пробелами и %-кодированием."""
    s = re.sub(r"\s+", "", unquote(text))
    s = s.replace("-", "+").replace("_", "/").rstrip("=")
    if not s or len(s) % 4 == 1:
        raise LinkError("не base64")
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LinkError("не base64") from exc


def b64text(text: str) -> str:
    """Base64 → строка UTF-8; LinkError, если это не base64 или не текст."""
    try:
        return b64decode(text).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LinkError("base64 не в UTF-8") from exc


def parse_query(query: str) -> dict:
    """k=v&k2=v2 → dict. Значения %-декодируются, '+' сохраняется; пустые отбрасываются;
    при повторе ключа берётся первое значение."""
    params = {}
    for pair in query.split("&"):
        key, sep, value = pair.partition("=")
        key, value = unquote(key).strip(), unquote(value).strip()
        if key and sep and value and key not in params:
            params[key] = value
    return params


def split_hostport(text: str) -> tuple[str, str]:
    """host:port / [v6]:port → (host без скобок, порт строкой)."""
    text = text.strip()
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep:
            raise LinkError("незакрытая скобка IPv6")
        return host, rest[1:] if rest.startswith(":") else ""
    host, sep, port = text.rpartition(":")
    return (host, port) if sep else (text, "")


def to_port(text: str) -> int:
    """Номер порта; '443/' и '443,1000-2000' дают 443."""
    m = re.match(r"\s*(\d+)", text or "")
    if not m or not 0 < int(m.group(1)) < 65536:
        raise LinkError(f"некорректный порт {text!r}")
    return int(m.group(1))


@dataclass
class Link:
    scheme: str
    userinfo: str           # как в ссылке, без %-декодирования (может быть base64)
    host: str
    port: str
    path: str
    params: dict = field(default_factory=dict)
    name: str = ""          # фрагмент, %-декодированный, как есть

    @property
    def user(self) -> str:
        return unquote(self.userinfo)

    def port_int(self) -> int:
        return to_port(self.port)


def split_link(link: str) -> Link:
    """Разобрать scheme://[userinfo@]host[:port][/path][?query][#name]."""
    scheme, sep, rest = link.strip().partition("://")
    if not sep:
        raise LinkError("нет схемы")
    rest, _, name = rest.partition("#")
    rest, _, query = rest.partition("?")
    # userinfo — до последнего '@' (в паролях и base64 бывают '/'), путь — после адреса
    userinfo, at, rest = rest.rpartition("@")
    if not at:
        userinfo = ""
    slash = rest.find("/")
    hostport, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    host, port = split_hostport(hostport)
    return Link(scheme.lower(), userinfo, host, port, path, parse_query(query),
                unquote(name))


def is_true(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def csv(value: str) -> list:
    """'h2,http/1.1' / '{h2,h3}' → список."""
    return [x.strip() for x in str(value).strip("{}[]").split(",") if x.strip()]


def none_like(value) -> bool:
    return value is None or str(value).strip().lower() in ("", "none", "null")


def tls_block(params: dict, *, server_name: str = "", insecure: bool = False) -> dict:
    """tls-объект sing-box из типовых параметров ссылки (sni/peer, alpn, fp, allowInsecure)."""
    sni = params.get("sni") or params.get("peer") or server_name
    tls = {"enabled": True,
           "insecure": insecure or is_true(params.get("allowInsecure", ""))
           or is_true(params.get("insecure", "")),
           "server_name": "" if none_like(sni) else sni}
    if not none_like(params.get("alpn")):
        tls["alpn"] = csv(params["alpn"])
    if not none_like(params.get("fp")):
        tls["utls"] = {"enabled": True, "fingerprint": params["fp"]}
    return tls


def add_reality(tls: dict, params: dict) -> None:
    """Reality поверх tls: pbk, sid; uTLS обязателен (отпечаток — из fp, если есть)."""
    reality = {"enabled": True, "public_key": params.get("pbk")}
    if not none_like(params.get("sid")):
        reality["short_id"] = params["sid"]
    tls["reality"] = reality
    tls.setdefault("utls", {"enabled": True})


_EARLY_DATA = re.compile(r"\?ed=(\d+)$")


def ws_transport(path: str, host: str = "") -> dict:
    """ws; хвост пути ?ed=N — early data в Sec-WebSocket-Protocol."""
    path = path or "/"
    transport = {"type": "ws", "path": path}
    m = _EARLY_DATA.search(path)
    if m:
        transport["path"] = path[:m.start()] or "/"
        transport["early_data_header_name"] = "Sec-WebSocket-Protocol"
        transport["max_early_data"] = int(m.group(1))
    if host:
        transport["headers"] = {"Host": host}
    return transport


def multiplex(params: dict):
    """Мультиплекс sing-box из protocol=smux|yamux|h2mux и max-streams/… (None, если нет)."""
    proto = params.get("protocol")
    if proto not in ("smux", "yamux", "h2mux"):
        return None
    mux = {"enabled": True, "protocol": proto}
    for src, dst in (("max-connections", "max_connections"), ("min-streams", "min_streams"),
                     ("max-streams", "max_streams")):
        if str(params.get(src, "")).isdigit():
            mux[dst] = int(params[src])
    if is_true(params.get("padding", "")):
        mux["padding"] = True
    return mux


# --- XHTTP (sing-box-lx, тег with_xhttp) ---------------------------------------------
# vless://…?type=xhttp&mode=…&path=…&extra=<json>: ключи extra перекрывают query.
# В transport попадают только пришедшие поля — у ядра свои значения по умолчанию.

_XHTTP_STR = {
    "xPaddingBytes": "x_padding_bytes",
    "sessionPlacement": "session_placement", "sessionKey": "session_key",
    "seqPlacement": "seq_placement", "seqKey": "seq_key",
    "uplinkDataPlacement": "uplink_data_placement", "uplinkDataKey": "uplink_data_key",
    "uplinkChunkSize": "uplink_chunk_size", "uplinkHTTPMethod": "uplink_http_method",
    "xPaddingKey": "x_padding_key", "xPaddingHeader": "x_padding_header",
    "xPaddingPlacement": "x_padding_placement", "xPaddingMethod": "x_padding_method",
}
_XHTTP_RANGE = {"scMaxEachPostBytes": "sc_max_each_post_bytes",
                "scMinPostsIntervalMs": "sc_min_posts_interval_ms"}
_XHTTP_FLAGS = {"noGRPCHeader": "no_grpc_header", "xPaddingObfsMode": "x_padding_obfs_mode"}
# xmux у части провайдеров приходит в camelCase; ядро знает только snake_case.
_XMUX_KEYS = {
    "cMaxReuseTimes": "c_max_reuse_times", "maxConcurrency": "max_concurrency",
    "maxConnections": "max_connections", "hKeepAlivePeriod": "h_keep_alive_period",
    "hMaxRequestTimes": "h_max_request_times", "hMaxReusableSecs": "h_max_reusable_secs",
}


def xhttp_range(value) -> str:
    """Диапазон в виде "min-max": 30 → "30-30", [1, 2] → "1-2", "1-2" как есть."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{int(value[0])}-{int(value[1])}"
    if isinstance(value, float):
        value = int(value)
    text = str(value)
    return text if "-" in text or isinstance(value, bool) else f"{text}-{text}"


def _extra(raw):
    for candidate in (raw, unquote(raw)):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _keepalive(value):
    """h_keep_alive_period — целое (не диапазон): "15-30" → 15, "-1" → -1."""
    text = str(value).strip()
    if "-" in text[1:] and not text.startswith("-"):     # диапазон — нижняя граница
        text = text.split("-")[0]
    return int(float(text))


def xhttp_transport(params: dict) -> dict:
    opts = dict(params)
    if params.get("extra"):
        opts.update(_extra(params["extra"]))
    transport = {"type": "xhttp", "path": str(opts.get("path") or "/").split("?", 1)[0] or "/"}
    for key in ("host", "mode"):
        if opts.get(key):
            transport[key] = opts[key]
    for src, dst in _XHTTP_STR.items():
        if opts.get(src) not in (None, ""):
            transport[dst] = opts[src]
    for src, dst in _XHTTP_FLAGS.items():
        if is_true(opts.get(src, "")):
            transport[dst] = True
    for src, dst in _XHTTP_RANGE.items():
        if opts.get(src) not in (None, ""):
            transport[dst] = xhttp_range(opts[src])
    xmux = {}
    raw_xmux = opts.get("xmux")
    for key, value in (raw_xmux.items() if isinstance(raw_xmux, dict) else ()):
        key = _XMUX_KEYS.get(key, key)
        if key == "h_keep_alive_period":
            try:
                xmux[key] = _keepalive(value)
            except (TypeError, ValueError):
                pass
        else:
            xmux[key] = xhttp_range(value)
    if xmux:
        transport["xmux"] = xmux
    return transport


def stream_transport(params: dict):
    """Транспорт v2ray-семейства по type=… (ws, grpc, http/h2, httpupgrade, quic, xhttp)."""
    kind = (params.get("type") or "").lower()
    host = params.get("host", "")
    if kind == "ws":
        return ws_transport(params.get("path", "/"), host)
    if kind == "grpc":
        return {"type": "grpc", "service_name": params.get("serviceName", "")}
    if kind in ("http", "h2"):
        transport = {"type": "http"}
        if host:
            transport["host"] = csv(host) if "," in host else host
        if params.get("path"):
            transport["path"] = params["path"]
        return transport
    if kind == "httpupgrade":
        transport = {"type": "httpupgrade", "path": params.get("path") or "/"}
        if host:
            transport["host"] = host
        return transport
    if kind == "quic":
        return {"type": "quic"}
    if kind == "xhttp":
        return xhttp_transport(params)
    return None
