"""vmess:// — JSON v2rayN в base64 (2dust/v2rayN wiki «Description of VMess share link»).

Поля: add, port, id, aid, scy, net (tcp|ws|h2|http|grpc|quic|httpupgrade), type (тип
заголовка для tcp), host, path, tls, sni, alpn, fp, ps (имя). Фрагмент #имя после base64,
если есть, важнее ps. Shadowrocket-вариант: vmess://base64(cipher:uuid@host:port)?remarks=…
"""

from __future__ import annotations

import json
from urllib.parse import unquote

from ._common import (LinkError, b64text, csv, is_true, none_like, split_hostport, split_link,
                      to_port, ws_transport)

_CIPHERS = {"auto", "none", "zero", "aes-128-gcm", "chacha20-poly1305", "aes-128-ctr"}


def _base(server, port, uuid, cipher, alter_id, name):
    if not server or not uuid:
        raise LinkError("нет адреса или uuid")
    return {"tag": name, "type": "vmess", "server": server, "server_port": to_port(str(port)),
            "uuid": uuid, "security": cipher if cipher in _CIPHERS else "auto",
            "alter_id": int(alter_id or 0),
            # xudp — значение ядра по умолчанию; поле явно входит в отпечаток ноды.
            "packet_encoding": "xudp"}


def _from_json(item: dict, name: str):
    node = _base(item.get("add"), item.get("port"), item.get("id"), item.get("scy") or "auto",
                 item.get("aid"), name or str(item.get("ps") or "").strip())
    net = str(item.get("net") or "tcp").lower()
    host, path = str(item.get("host") or ""), str(item.get("path") or "")
    if not none_like(item.get("tls")):
        sni = item.get("sni") or (host if net not in ("h2", "http") else "")
        tls = {"enabled": True, "insecure": is_true(item.get("allowInsecure", "")),
               "server_name": sni}
        if not none_like(item.get("alpn")):
            tls["alpn"] = csv(item["alpn"])
        if not none_like(item.get("fp")):
            tls["utls"] = {"enabled": True, "fingerprint": item["fp"]}
        node["tls"] = tls
    header = str(item.get("type") or "none").lower()
    if net in ("h2", "http") or (net == "tcp" and header == "http"):
        transport = {"type": "http"}
        if host:
            transport["host"] = csv(host) if "," in host else host
        http_path = path.split("?")[0]
        if http_path:
            transport["path"] = http_path
        node["transport"] = transport
    elif net == "ws":
        node["transport"] = ws_transport(path, host)
    elif net == "httpupgrade":
        node["transport"] = {"type": "httpupgrade", "path": path or "/"}
        if host:
            node["transport"]["host"] = host
    elif net == "grpc":
        node["transport"] = {"type": "grpc", "service_name": path}
    elif net == "quic":
        node["transport"] = {"type": "quic"}
    return node


def _shadowrocket(link):
    cred, _, hostport = b64text(link.host + link.path).rpartition("@")
    cipher, _, uuid = cred.rpartition(":")
    server, port = split_hostport(hostport)
    p = link.params
    node = _base(server, port, uuid, cipher or "auto", p.get("alterId"), p.get("remarks", ""))
    if is_true(p.get("tls", "")) or p.get("security") == "tls":
        node["tls"] = {"enabled": True, "insecure": is_true(p.get("allowInsecure", "")),
                       "server_name": p.get("sni") or p.get("peer", "")}
    if p.get("obfs") == "websocket" or p.get("type") == "ws":
        host = p.get("host", "")
        try:
            host = json.loads(p.get("obfsParam", "")).get("Host") or host
        except (ValueError, AttributeError):
            pass
        node["transport"] = ws_transport(p.get("path", "/"), host)
    return node


def parse(text: str):
    body, _, name = text.strip()[len("vmess://"):].partition("#")
    if "?" in body:
        return _shadowrocket(split_link(text))
    try:
        item = json.loads(b64text(body))
    except ValueError as exc:
        raise LinkError("vmess: не JSON") from exc
    if not isinstance(item, dict):
        raise LinkError("vmess: не объект")
    return _from_json(item, unquote(name))
