#!/bin/sh
#
# update-rules.sh — обновить base.json и правила sing-box (.srs + source-наборы доменов).
# Выделено из update-singbox-config.sh без изменения списка источников. sing-box НЕ
# перезапускает: если что-то реально изменилось, оставляет маркер $RULES_MARK — по нему
# apply-nodes.sh перезапустит sing-box даже при неизменных нодах.
#
#   update-rules.sh
#
# Неудачная загрузка правила не затирает рабочий файл (временный файл → замена только при
# успехе и непустом ответе) и не прерывает обновление.

set -u

REPO_DIR="${REPO_DIR:-/root/singbox-repo}"
TARGET_DIR="${TARGET_DIR:-/etc/sing-box}"
RULES_DIR="$TARGET_DIR/rules"
RULES_MARK="${RULES_MARK:-/tmp/nodes-rules-changed}"
CHANGED=""

log() { echo "[rules] $*"; logger -t singbox-rules "$*" 2>/dev/null || true; }

# Заменить dest содержимым src, если отличается; отметить изменение.
replace_if_changed() {
    src="$1"; dest="$2"
    if ! cmp -s "$src" "$dest"; then
        cp "$src" "$dest.tmp" && mv "$dest.tmp" "$dest"
        CHANGED="$CHANGED $(basename "$dest")"
    fi
}

fetch_rule() {
    dest="$1"; url="$2"
    tmp="$(mktemp)"
    if curl -fsSL -o "$tmp" "$url" && [ -s "$tmp" ]; then
        replace_if_changed "$tmp" "$dest"
    else
        log "не скачалось, оставляю прежний: $dest"
    fi
    rm -f "$tmp"
}

mkdir -p "$RULES_DIR"

if cd "$REPO_DIR" && git fetch origin; then
    git checkout origin/master -- ru-also-domains.json us-domains.json base.json \
        || log "git checkout не удался — беру файлы, что есть в $REPO_DIR"
else
    log "git fetch не удался — беру файлы, что есть в $REPO_DIR"
fi
replace_if_changed "$REPO_DIR/base.json" "$TARGET_DIR/base.json"

fetch_rule "$RULES_DIR/category-ru.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/category-ru.srs
fetch_rule "$RULES_DIR/ru.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geoip/ru.srs
fetch_rule "$RULES_DIR/ruleset-domain-oisd_nsfw.srs" https://raw.githubusercontent.com/burjuyz/RuRulesets/main/ruleset-domain-oisd_nsfw.srs
fetch_rule "$RULES_DIR/ruleset-domain-torrent_trackres.srs" https://github.com/burjuyz/RuRulesets/raw/main/ruleset-domain-torrent_trackres.srs
fetch_rule "$RULES_DIR/ruleset-domain-oisd_big.srs" https://github.com/burjuyz/RuRulesets/raw/main/ruleset-domain-oisd_big.srs
fetch_rule "$RULES_DIR/google-deepmind.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/google-deepmind.srs
fetch_rule "$RULES_DIR/google-gemini.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/google-gemini.srs
# Весь Google (кроме YouTube) идёт вместе с AI: Google сверяет страну по всем своим доменам.
fetch_rule "$RULES_DIR/google.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/google.srs
fetch_rule "$RULES_DIR/youtube.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/youtube.srs
# IP Google: QUIC к ним отклоняем — у Chrome QUIC не снифится (нет домена → final мимо NL).
fetch_rule "$RULES_DIR/geoip-google.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geoip/google.srs
fetch_rule "$RULES_DIR/zoom.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/zoom.srs
fetch_rule "$RULES_DIR/anthropic.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/anthropic.srs
# Все зарубежные AI-сервисы (OpenAI, xAI/Grok, Perplexity, Copilot, Cursor, Mistral, Meta AI…).
fetch_rule "$RULES_DIR/category-ai.srs" https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/category-ai-%21cn.srs
fetch_rule "$RULES_DIR/hagezi-bypass.srs" https://cdn.jsdelivr.net/gh/razaxq/dns-blocklists-sing-box@rule-set/hagezi-bypass.srs
# ru-app-list — только для клиентских конфигов (роутерный base.json его не подключает),
# но лежит в общем каталоге правил, откуда caddy раздаёт клиентам.
fetch_rule "$RULES_DIR/ru-app-list.srs" https://raw.githubusercontent.com/legiz-ru/sb-rule-sets/main/ru-app-list.srs
# Белые списки мобильного интернета РФ (hxehex/russia-mobile-internet-whitelist, сборка .srs
# Master-Yoba) — тоже только для клиентов (режим «БЕЛЫЕ СПИСКИ»).
fetch_rule "$RULES_DIR/russia-mobile-whitelist-domains.srs" https://github.com/Master-Yoba/Russia-mobile-whitelist-geo-builder/releases/latest/download/russia-mobile-whitelist-domains.srs
fetch_rule "$RULES_DIR/russia-mobile-whitelist-cidr.srs" https://github.com/Master-Yoba/Russia-mobile-whitelist-geo-builder/releases/latest/download/russia-mobile-whitelist-cidr.srs

# Source-наборы (format: source): читают и роутерный base.json, и клиенты через caddy.
replace_if_changed "$REPO_DIR/us-domains.json" "$RULES_DIR/us-domains.json"
replace_if_changed "$REPO_DIR/ru-also-domains.json" "$RULES_DIR/ru-also-domains.json"

if [ -n "$CHANGED" ]; then
    touch "$RULES_MARK"
    log "обновлено:$CHANGED (маркер для apply-nodes: $RULES_MARK)"
else
    log "правила без изменений"
fi
