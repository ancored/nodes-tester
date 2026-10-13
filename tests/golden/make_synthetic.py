"""Генератор синтетических golden-наборов (выдуманные ноды — безопасно хранить в репозитории).

    python tests/golden/make_synthetic.py        # пишет tests/fixtures/golden/{synthetic,synthetic-wh}/
    python tests/golden/harness.py golden --project . --set tests/fixtures/golden/synthetic
    python tests/golden/harness.py golden --project . --set tests/fixtures/golden/synthetic-wh

Покрытие: vless reality/grpc/xhttp/голый (отбрасывается), vmess ws tls, trojan ws без host и с
%-паролем, hy2 (порт и диапазон портов), ss, tuic, anytls, страна не определяется (undef),
нода с выходом в RU (страну больше не фильтруем), дубль ноды (dedupe), clash-yaml подписка, упавшая
подписка, AWG-папка, inline-подписка со своими нодами; две ветви config.json
(router: группы eu/us/other; clients: exclude_protocols и одна global-auto-out).
"""

import base64
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "tests", "fixtures", "golden")

U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"
PBK = "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyMDA"


def _vmess(ps, add, cc_host):
    j = {"v": "2", "ps": ps, "add": add, "port": "443", "id": U2, "aid": "0", "scy": "auto",
         "net": "ws", "type": "none", "host": cc_host, "path": "/vm", "tls": "tls", "sni": cc_host}
    return "vmess://" + base64.b64encode(json.dumps(j, ensure_ascii=False).encode()).decode()


ALPHA = "\n".join([
    f"vless://{U1}@10.0.0.1:443?security=reality&sni=www.example.com&fp=chrome&pbk={PBK}"
    f"&sid=ab12&type=tcp&flow=xtls-rprx-vision#🇩🇪 Germany | Reality",
    f"vless://{U1}@10.0.0.2:443?security=reality&sni=www.example.com&fp=chrome&pbk={PBK}"
    f"&sid=cd34&type=grpc&serviceName=grpc#🇳🇱 Netherlands ChatGPT",
    f"vless://{U1}@10.0.0.3:443?security=tls&sni=xh.example.com&type=xhttp&mode=packet-up"
    f"&path=%2Fxh#🇫🇮 Finland XHTTP",
    f"vless://{U1}@10.0.0.4:80?type=tcp&security=none#🇳🇱 plain vless",
    _vmess("🇺🇸 USA Netflix", "10.0.0.5", "vm.example.com"),
    "trojan://pa%40ss@10.0.0.6:443?type=ws&path=%2F&sni=tr.example.com#🇺🇸 USA trojan ws",
    "trojan://plain@10.0.0.7:443?sni=tr2.example.com#🇬🇧 United Kingdom trojan",
    "hysteria2://pw@10.0.0.8:8443?sni=hy.example.com&insecure=1#🇸🇪 Sweden",
    "hysteria2://pw@10.0.0.9:20000-30000?sni=hy.example.com#🇯🇵 Japan ports",
    "ss://" + base64.b64encode(b"aes-256-gcm:sspass").decode() + "@10.0.0.10:8388#🇷🇺 Russia ss",
    "ss://" + base64.b64encode(b"chacha20-ietf-poly1305:sspass2").decode() + "@10.0.0.11:8388#🇵🇱 Poland ss",
    f"tuic://{U2}:pw@10.0.0.12:443?sni=tu.example.com&congestion_control=bbr&alpn=h3#🇨🇭 Switzerland tuic",
    "anytls://pw@10.0.0.13:443?sni=at.example.com#🇦🇹 Austria anytls",
    f"vless://{U1}@10.0.0.14:443?security=reality&sni=www.example.com&fp=chrome&pbk={PBK}"
    f"&sid=ef56&type=tcp#Somewhere unknown",
    f"vless://{U1}@10.0.0.1:443?security=reality&sni=www.example.com&fp=chrome&pbk={PBK}"
    f"&sid=ab12&type=tcp&flow=xtls-rprx-vision#🇩🇪 Germany | Reality dup",
])

BETA_YAML = """proxies:
  - name: "🇫🇷 France trojan"
    type: trojan
    server: 10.0.1.1
    port: 443
    password: yamlpass
    sni: fr.example.com
  - name: "🇪🇸 Spain vmess"
    type: vmess
    server: 10.0.1.2
    port: 443
    uuid: 33333333-3333-4333-8333-333333333333
    alterId: 0
    cipher: auto
    tls: true
    network: ws
    ws-opts:
      path: /es
      headers:
        Host: es.example.com
"""

AWG_CONF = """[Interface]
Address = 10.8.0.2/32
DNS = 1.1.1.1
PrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MDA=
Jc = 4
Jmin = 40
Jmax = 70
S1 = 10
S2 = 20
H1 = 1
H2 = 2
H3 = 3
H4 = 4
[Peer]
PublicKey = cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHViMDA=
AllowedIPs = 0.0.0.0/0
Endpoint = 10.0.2.1:51820
PersistentKeepalive = 25
"""

USER_NODES = [{
    "type": "vless", "tag": "Custom-NL", "server": "10.0.3.1", "server_port": 443, "uuid": U1,
    "flow": "xtls-rprx-vision",
    "tls": {"enabled": True, "server_name": "www.example.com",
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {"enabled": True, "public_key": PBK, "short_id": "aa"}}}]


SUBS = [
    {"url": "https://sub.example/alpha", "tag": "ALPHA", "user_agent": "curl"},
    {"url": "https://sub.example/beta.yaml", "tag": "BETA"},
    {"url": "https://sub.example/gamma-down", "tag": "GAMMA"},
    {"url": "https://sub.example/disabled", "tag": "OFF", "enabled": False},
    {"type": "folder", "path": "awg", "format": "awg", "ext": ".conf", "tag": "AWG"},
]

RESPONSES = {
    "http": {
        "https://sub.example/alpha": {"status": 200,
                                      "b64": base64.b64encode(base64.b64encode(ALPHA.encode())).decode()},
        "https://sub.example/beta.yaml": {"status": 200, "b64": base64.b64encode(BETA_YAML.encode()).decode()},
        "https://sub.example/gamma-down": None,
    },
    "happ": {},
}

GROUP_OPTS = {"selector": {"interrupt_exist_connections": True},
              "urltest": {"interval": "5m", "tolerance": 100, "idle_timeout": "5m",
                          "interrupt_exist_connections": True}}


def _subs(branch=None):
    subs = SUBS + [{"tag": "Custom", "type": "inline", "outbounds": USER_NODES}]
    return [dict(sub, branch=branch) for sub in subs] if branch else subs


SETS = {
    "synthetic": {
        "subscriptions": _subs(),
        "groups": [{"tag": "eu"}, {"tag": "us"}, {"tag": "other"}],
        "pipeline": {"router": {"exclude_types": ["shadowsocksr"], "domain_resolver": "bootstrap",
                                **GROUP_OPTS}},
    },
    "synthetic-wh": {
        "subscriptions": _subs("clients"),
        "pipeline": {"clients": {"exclude_types": ["shadowsocksr"], "exclude_protocols": ["xhttp", "wg"],
                                 "domain_resolver": "dns-whitelist", **GROUP_OPTS}},
    },
}


def _dump(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    for name, spec in SETS.items():
        d = os.path.join(OUT, name)
        if os.path.exists(d):
            shutil.rmtree(d)
        cfg = os.path.join(d, "config")
        os.makedirs(os.path.join(cfg, "awg"))
        _dump(os.path.join(cfg, "config.json"), spec)
        with open(os.path.join(cfg, "awg", "pl.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(AWG_CONF)
        _dump(os.path.join(d, "responses.json"), RESPONSES)
        print("written", d)


if __name__ == "__main__":
    main()
