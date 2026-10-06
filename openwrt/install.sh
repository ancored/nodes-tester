#!/bin/sh
#
# Минимальная установка на чистый OpenWrt 25.x: ядро sing-box-lx и nodes-tester.
#
#   wget -qO- https://raw.githubusercontent.com/ancored/nodes-tester/master/openwrt/install.sh | sh
#   wget -qO- …/install.sh | sh -s -- 'https://provider.example/sub/TOKEN'
#
# Конфиг sing-box минимальный: TUN с перехватом трафика и DNS роутера и LAN, sniff, весь трафик —
# напрямую (единственный пресет), DNS — системный резолвер роутера. Подписки для этого не нужны;
# с подпиской скрипт сразу собирает ноды и применяет конфиг.
#
#   SINGBOX_LX_VERSION=1.14.2-lx.11   версия ядра вместо последней

set -eu

LX_REPO=Leadaxe/sing-box-lx
NT_RAW=https://raw.githubusercontent.com/ancored/nodes-tester/master
NT_FEED=https://ancored.github.io/nodes-tester/packages.adb
SB_DIR=/etc/sing-box
NT_DIR=/etc/nodes-tester
NODES=/etc/sing-box-subscribe/nodes.json
SUB_URL="${1:-}"

log() { echo "[install] $*"; }
die() { echo "[install] ОШИБКА: $*" >&2; exit 1; }

command -v apk >/dev/null || die "нужен OpenWrt 25.x с пакетным менеджером apk"
[ "$(id -u)" = 0 ] || die "запускайте от root"
# Существующую установку не трогаем: её конфиги и база — дело владельца.
if [ -e "$SB_DIR/config.json" ] || [ -e "$NT_DIR/singbox/base.json" ] \
	|| apk info -e sing-box >/dev/null 2>&1 || apk info -e nodes-tester >/dev/null 2>&1; then
	die "sing-box или nodes-tester уже установлены — используйте инструкцию openwrt/README.md"
fi

case "$(apk --print-arch)" in
	x86_64) ARCH=amd64 ;;
	aarch64*) ARCH=arm64 ;;
	arm_*) ARCH=armv7 ;;
	mipsel_*) ARCH=mipsle-softfloat ;;
	mips_*) ARCH=mips-softfloat ;;
	*) die "нет сборки sing-box-lx для архитектуры $(apk --print-arch)" ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- sing-box-lx ---------------------------------------------------------------
log "зависимости"
apk update >/dev/null
apk add ca-bundle kmod-tun kmod-inet-diag >/dev/null

VER="${SINGBOX_LX_VERSION:-$(wget -qO- "https://api.github.com/repos/$LX_REPO/releases/latest" \
	| sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')}"
[ -n "$VER" ] || die "не удалось узнать последнюю версию sing-box-lx"
TB="sing-box-$VER-linux-$ARCH.tar.gz"
log "sing-box-lx $VER ($ARCH)"
wget -qO "$TMP/$TB" "https://github.com/$LX_REPO/releases/download/v$VER/$TB" || die "не скачался $TB"
wget -qO "$TMP/SHA256SUMS" "https://github.com/$LX_REPO/releases/download/v$VER/SHA256SUMS" \
	|| die "не скачался SHA256SUMS"
(cd "$TMP" && grep " $TB\$" SHA256SUMS | sha256sum -c >/dev/null) || die "контрольная сумма $TB не совпала"
tar -xzf "$TMP/$TB" -C "$TMP"
cp "$TMP/sing-box-$VER-linux-$ARCH/sing-box" /usr/bin/sing-box
chown root:root /usr/bin/sing-box
chmod 755 /usr/bin/sing-box
sing-box version | head -1

cat > /etc/init.d/sing-box <<'EOF'
#!/bin/sh /etc/rc.common

USE_PROCD=1
START=99

start_service() {
	mkdir -p /usr/share/sing-box
	procd_open_instance
	procd_set_param command /usr/bin/sing-box run -c /etc/sing-box/config.json -D /usr/share/sing-box
	procd_set_param file /etc/sing-box/config.json
	procd_set_param stdout 1
	procd_set_param stderr 1
	procd_set_param respawn
	procd_close_instance
}
EOF
chmod 755 /etc/init.d/sing-box

# --- nodes-tester --------------------------------------------------------------
log "nodes-tester"
wget -qO /etc/apk/keys/nodes-tester.pem "$NT_RAW/openwrt/nodes-tester.pem" || die "не скачался ключ пакетов"
FEEDS=/etc/apk/repositories.d/customfeeds.list
grep -qxF "$NT_FEED" "$FEEDS" 2>/dev/null || echo "$NT_FEED" >> "$FEEDS"
apk update >/dev/null
apk add nodes-tester

# --- минимальная база sing-box -------------------------------------------------
SECRET="$(tr -dc 'a-f0-9' < /dev/urandom | head -c 32)"
LAN_IP="$(uci -q get network.lan.ipaddr | cut -d/ -f1)"
[ -n "$LAN_IP" ] || die "не найден адрес LAN (network.lan.ipaddr)"
LAN_NET="$(uci -q get network.lan.ipaddr)"
case "$LAN_NET" in */*) ;; *) LAN_NET="$LAN_NET/$(uci -q get network.lan.netmask || echo 24)" ;; esac
LAN_DEV="$(uci -q get network.lan.device || echo br-lan)"
WAN_DEV="$(ifstatus wan 2>/dev/null | jsonfilter -e '@.l3_device' 2>/dev/null || true)"
# dnsmasq — системный резолвер, которым пользуется DNS-сервер local; его запросы к провайдеру
# не должны снова попасть в TUN, иначе петля.
DNSMASQ_UID="$(id -u dnsmasq 2>/dev/null || echo 453)"
umask 077
python3 - "$NT_DIR/singbox/base.json" "$SECRET" "$LAN_NET" "$LAN_DEV" "$WAN_DEV" "$DNSMASQ_UID" <<'EOF'
import ipaddress, json, sys
path, secret, lan_net, lan_dev, wan_dev, dnsmasq_uid = sys.argv[1:]
route = {
    "rules": [
        {"action": "sniff"},
        {"protocol": "dns", "action": "hijack-dns"},
        {"ip_cidr": [str(ipaddress.ip_interface(lan_net).network)], "outbound": "direct-lan"},
        {"inbound": "nodes-tester-in", "outbound": "nodes-tester"},
    ],
    "default_domain_resolver": "bootstrap",
}
if wan_dev:
    route["default_interface"] = wan_dev
else:
    route["auto_detect_interface"] = True
base = {
    "log": {"level": "warn", "timestamp": True},
    "dns": {"servers": [{"type": "local", "tag": "bootstrap"}], "final": "bootstrap"},
    "inbounds": [
        {"type": "tun", "tag": "tun-in", "interface_name": "tun0", "address": ["172.18.0.1/30"],
         "auto_route": True, "auto_redirect": True, "strict_route": True,
         "exclude_uid": [int(dnsmasq_uid)]},
        {"type": "socks", "tag": "nodes-tester-in", "listen": "127.0.0.1", "listen_port": 2080},
    ],
    "outbounds": [
        {"type": "direct", "tag": "direct-out"},
        {"type": "direct", "tag": "direct-lan", "bind_interface": lan_dev},
    ],
    "route": route,
    "services": [{"type": "api", "tag": "api", "listen": "127.0.0.1", "listen_port": 9090,
                  "secret": secret}],
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False, indent=2)
EOF
cat > "$NT_DIR/singbox/presets/direct.json" <<'EOF'
{
  "_preset": {
    "title": "Всё напрямую",
    "description": "Весь трафик через sing-box уходит напрямую через WAN",
    "enabled": true,
    "priority": 900
  },
  "route": { "final": "direct-out" }
}
EOF
echo '{ "version": 1, "rules": [] }' > "$NT_DIR/singbox/rules.json"

# Заглушка селектора тестера до первой сборки нод: без него база не проходит check.
mkdir -p "$SB_DIR" "$(dirname "$NODES")"
echo '{ "outbounds": [ { "type": "selector", "tag": "nodes-tester", "outbounds": ["direct-out"] } ] }' > "$NODES"
cp "$NT_DIR/singbox/base.json" "$SB_DIR/base.json"
PYTHONPATH=/usr/lib/nodes-tester python3 -m nodes_admin.presets assemble --base "$SB_DIR/base.json" \
	--nodes "$NODES" --dir "$NT_DIR/singbox/presets" --out "$SB_DIR/config.json"
sing-box check -c "$SB_DIR/config.json"
umask 022

# --- конфиг тестера ------------------------------------------------------------
python3 - "$NT_DIR/config.json" "$SECRET" "$LAN_IP" <<'EOF'
import json, sys
path, secret, host = sys.argv[1:]
with open(path, encoding="utf-8") as f:
    cfg = json.load(f)
cfg["box_api"]["secret"] = secret
cfg["dashboard"].update(enabled=True, host=host)
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
EOF

/etc/init.d/sing-box enable
/etc/init.d/sing-box start
# Связность через TUN: DNS роутера и HTTPS. Нет — sing-box останавливается, сеть как была.
ok=0
for _ in 1 2 3 4 5 6; do
	sleep 5
	if pidof sing-box >/dev/null && ip link show tun0 >/dev/null 2>&1 && nft list table inet sing-box >/dev/null 2>&1 		&& nslookup www.gstatic.com 127.0.0.1 >/dev/null 2>&1 && wget -qO /dev/null https://www.gstatic.com/generate_204; then
		ok=1; break
	fi
done
if [ "$ok" = 0 ]; then
	/etc/init.d/sing-box stop
	/etc/init.d/sing-box disable
	die "через sing-box нет связности — служба остановлена и выключена; журнал: logread -e sing-box"
fi
log "sing-box работает, трафик идёт через TUN напрямую"
uci set nodes-tester.tester.enabled=1
uci commit nodes-tester
/etc/init.d/nodes-tester enable
/etc/init.d/nodes-tester start

if [ -n "$SUB_URL" ]; then
	python3 - "$NT_DIR/config-main/providers.json" "$SUB_URL" <<'EOF'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"subscribes": [{"tag": "SUB", "url": sys.argv[2]}]}, f, indent=2)
EOF
	log "сборка нод из подписки"
	nodes-tester pipeline router || log "конвейер не прошёл — подписку проверьте в админке"
fi

TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dashboard"]["token"])' "$NT_DIR/config.json")"
log "готово"
echo "  админка: http://$LAN_IP:8088/"
echo "  токен:   $TOKEN"
[ -n "$SUB_URL" ] || echo "  дальше: «Подписки» — добавить свою, «Конвейер» — «Применить»"
