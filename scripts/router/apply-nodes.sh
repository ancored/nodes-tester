#!/bin/sh
#
# apply-nodes.sh — применить nodes.json к sing-box роутера: merge base.json + nodes.json →
# sing-box check → замена config.json → рестарт. Замена старого хвоста update-singbox-config.sh.
#
#   apply-nodes.sh [--dry-run] [--force] NODES_JSON
#
#   --dry-run  всё то же (merge + check + diff), но ничего не заменяет и не перезапускает;
#              кандидат кладётся в $CANDIDATE для просмотра
#   --force    перезапустить, даже если итоговый конфиг не изменился
#
# sing-box перезапускается ТОЛЬКО если: итоговый config.json изменился, или update-rules.sh
# оставил маркер «правила обновились» ($RULES_MARK), или --force. Невалидный конфиг
# (merge/check упали) — работающий config.json не трогается, exit 1. Если после рестарта
# sing-box не поднялся — возвращается прежний config.json и sing-box перезапускается на нём.
#
# Коды выхода: 0 — применено или менять нечего; 1 — ошибка (боевой конфиг цел или возвращён).

set -u

TARGET_DIR="${TARGET_DIR:-/etc/sing-box}"
BASE="$TARGET_DIR/base.json"
CONFIG="$TARGET_DIR/config.json"
RULES_MARK="${RULES_MARK:-/tmp/nodes-rules-changed}"
CANDIDATE="${CANDIDATE:-/root/nodes-data/config.candidate.json}"
DRY=0; FORCE=0; NODES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) NODES="$1" ;;
    esac
    shift
done

log() { echo "[apply] $*"; logger -t nodes-apply "$*" 2>/dev/null || true; }
die() { log "ОШИБКА: $*"; exit 1; }

[ -n "$NODES" ] || die "не указан nodes.json"
[ -f "$NODES" ] || die "нет файла: $NODES"
[ -f "$BASE" ] || die "нет базы: $BASE"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

sing-box merge "$TMP" -c "$BASE" -c "$NODES" >/dev/null || die "sing-box merge не удался — работающий конфиг не тронут"
sing-box check -c "$TMP" || die "итоговый конфиг не прошёл sing-box check — работающий конфиг не тронут"

# Сводка: какие теги outbounds/endpoints появятся и исчезнут.
python3 - "$CONFIG" "$TMP" <<'EOF'
import json, sys
def tags(p):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {o.get("tag") for o in d.get("outbounds", []) + d.get("endpoints", [])}
old, new = tags(sys.argv[1]), tags(sys.argv[2])
print(f"[apply] теги: было {len(old)}, станет {len(new)}, +{len(new - old)} −{len(old - new)}")
for t in sorted(new - old)[:10]:
    print(f"[apply]   + {t}")
for t in sorted(old - new)[:10]:
    print(f"[apply]   − {t}")
EOF

REASON=""
cmp -s "$TMP" "$CONFIG" || REASON="конфиг изменился"
[ -f "$RULES_MARK" ] && REASON="${REASON:+$REASON, }обновились правила"
[ "$FORCE" = 1 ] && REASON="${REASON:+$REASON, }--force"

if [ -z "$REASON" ]; then
    log "без изменений — sing-box не трогаю"
    exit 0
fi

if [ "$DRY" = 1 ]; then
    mkdir -p "$(dirname "$CANDIDATE")"
    cp "$TMP" "$CANDIDATE"
    log "DRY-RUN: применил бы ($REASON); кандидат: $CANDIDATE"
    exit 0
fi

cp -p "$CONFIG" "$CONFIG.prev"
mv "$TMP" "$CONFIG"
/etc/init.d/sing-box restart
sleep 5
if pidof sing-box >/dev/null; then
    rm -f "$RULES_MARK"
    log "применено и sing-box перезапущен ($REASON); прежний конфиг: $CONFIG.prev"
    exit 0
fi

log "sing-box не поднялся на новом конфиге — возвращаю прежний"
cp -p "$CONFIG.prev" "$CONFIG"
/etc/init.d/sing-box restart
sleep 5
pidof sing-box >/dev/null && die "откатились на прежний config.json, sing-box работает" \
                          || die "sing-box не поднялся и на прежнем конфиге — нужна ручная проверка"
