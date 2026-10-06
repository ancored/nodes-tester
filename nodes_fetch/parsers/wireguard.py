"""wireguard:// (wg://) — WireGuard в формате v2rayN.

wireguard://privateKey@host:port?publickey=…&address=10.0.0.2/32,fd00::2/128
            &reserved=1,2,3&mtu=1280&presharedkey=…#name
Ключи регистронезависимы (publicKey/publickey, ip/address). Узел — того же вида,
что у AWG-файлов: в сборке он уходит в endpoints.
"""

from __future__ import annotations

from urllib.parse import unquote

from ._common import LinkError, csv, split_link

DEFAULT_KEEPALIVE = 30


def _address(value: str) -> list:
    out = []
    for addr in csv(value):
        out.append(addr if "/" in addr else addr + ("/128" if ":" in addr else "/32"))
    return out


def parse(text: str):
    link = split_link(text)
    p = {k.lower(): v for k, v in link.params.items()}
    private_key = p.get("privatekey") or unquote(link.userinfo)
    if not link.host or not private_key or not p.get("publickey"):
        raise LinkError("нет адреса или ключей")
    peer = {"address": link.host, "port": link.port_int(), "public_key": p["publickey"],
            "allowed_ips": ["0.0.0.0/0"], "persistent_keepalive_interval": DEFAULT_KEEPALIVE}
    if p.get("presharedkey"):
        peer["pre_shared_key"] = p["presharedkey"]
    if p.get("reserved"):
        peer["reserved"] = [int(x) for x in csv(p["reserved"])]
    node = {"tag": link.name, "type": "wireguard", "private_key": private_key,
            "address": _address(p.get("address") or p.get("ip") or ""), "peers": [peer]}
    if not node["address"]:
        raise LinkError("нет адреса интерфейса")
    if p.get("mtu", "").isdigit():
        node["mtu"] = int(p["mtu"])
    return node
