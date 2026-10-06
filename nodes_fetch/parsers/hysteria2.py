"""hysteria2:// (hy2://) — URI-схема Hysteria 2 (v2.hysteria.network/docs/developers/URI-Scheme).

hysteria2://auth@host:port[,port|lo-hi…]/?sni=…&insecure=1&obfs=salamander&obfs-password=…#name
Порты-диапазоны (port hopping) → server_ports "lo:hi"; mport=… — то же параметром.
upmbps/downmbps — только если заданы: с ними ядро включает Brutal, без них — BBR.
"""

from __future__ import annotations

import re

from ._common import LinkError, csv, is_true, none_like, split_link


def _ports(spec: str):
    """'443,5000-6000' → (443, ['5000:6000']); '5000-6000' → (5000, ['5000:6000'])."""
    single, ranges = [], []
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+-\d+", part):
            ranges.append(part.replace("-", ":"))
        elif part.isdigit():
            single.append(int(part))
    if not single and not ranges:
        raise LinkError(f"некорректный порт {spec!r}")
    port = single[0] if single else int(ranges[0].split(":")[0])
    return port, ranges


def _mbps(value):
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


def parse(text: str):
    link = split_link(text)
    p = link.params
    port, ranges = _ports(link.port or "443")
    if not link.host:
        raise LinkError("нет адреса")
    node = {"tag": link.name,
            "type": "hysteria2",
            "server": link.host,
            "server_port": port,
            "password": p.get("auth") or link.user}
    if not ranges and p.get("mport"):
        ranges = [r.replace("-", ":") for r in csv(p["mport"])]
    if ranges:
        node["server_ports"] = ranges
    for key, field in (("upmbps", "up_mbps"), ("downmbps", "down_mbps")):
        mbps = _mbps(p.get(key))
        if mbps:
            node[field] = mbps
    tls = {"enabled": True}
    sni = p.get("sni") or p.get("peer")
    if not none_like(sni):
        tls["server_name"] = sni
    tls["insecure"] = is_true(p.get("insecure", "")) or is_true(p.get("allowInsecure", ""))
    # ALPN Hysteria 2 — h3; явное значение входит в отпечаток ноды
    tls["alpn"] = csv(p["alpn"]) if not none_like(p.get("alpn")) else ["h3"]
    node["tls"] = tls
    if not none_like(p.get("obfs")):
        node["obfs"] = {"type": p["obfs"], "password": p.get("obfs-password", "")}
    return node
