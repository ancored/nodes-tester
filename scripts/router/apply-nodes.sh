#!/bin/sh
#
# apply-nodes.sh — применить nodes.json к sing-box роутера: merge base.json + nodes.json →
# sing-box check → замена config.json → рестарт. Замена старого хвоста update-singbox-config.sh.
#
#   apply-nodes.sh [--dry-run] [--force] NODES_JSON
#   apply-nodes.sh --health          только проверить связность работающего sing-box
#
#   --dry-run  всё то же (merge + check + diff), но ничего не заменяет и не перезапускает;
#              кандидат кладётся в $CANDIDATE для просмотра
#   --force    перезапустить, даже если итоговый конфиг не изменился
#
# sing-box перезапускается ТОЛЬКО если: итоговый config.json изменился, или update-rules.sh
# оставил маркер «правила обновились» ($RULES_MARK), или --force. Невалидный конфиг
# (merge/check упали) — работающий config.json не трогается, exit 1.
#
# После рестарта — проверка РЕАЛЬНОЙ связности (до $HEALTH_WAIT с): через Clash API sing-box
# делает запрос к $HEALTH_URL через боевую группу $HEALTH_GROUP. Не прошла (процесс не
# поднялся или трафик не ходит) → автоматически возвращается прежний config.json и sing-box
# перезапускается на нём. Пока sing-box перезапускается, сеть (и удалённая сессия) может
# пропасть — поэтому скрипт не зависит от того, кто его запустил; запускать его вручную
# стоит отвязанным от сессии (nohup/setsid, см. switch-to-pipeline.sh).
#
# Коды выхода: 0 — применено или менять нечего; 1 — ошибка (боевой конфиг цел или возвращён).

set -u

TARGET_DIR="${TARGET_DIR:-/etc/sing-box}"
BASE="$TARGET_DIR/base.json"
CONFIG="$TARGET_DIR/config.json"
RULES_MARK="${RULES_MARK:-/tmp/nodes-rules-changed}"
CANDIDATE="${CANDIDATE:-${DATA:-/root/nodes-data}/config.candidate.json}"
HEALTH_GROUP="${HEALTH_GROUP:-global-auto-out}"
HEALTH_URL="${HEALTH_URL:-https://www.gstatic.com/generate_204}"
HEALTH_WAIT="${HEALTH_WAIT:-60}"
DRY=0; FORCE=0; HEALTH_ONLY=0; NODES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --force) FORCE=1 ;;
        --health) HEALTH_ONLY=1 ;;
        -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) NODES="$1" ;;
    esac
    shift
done

log() { echo "[apply] $(date '+%F %T') $*"; logger -t nodes-apply "$*" 2>/dev/null || true; }
die() { log "ОШИБКА: $*"; exit 1; }

# Один запрос проверки: Clash API отвечает, и через $HEALTH_GROUP реально проходит запрос
# к $HEALTH_URL. Адрес и секрет Clash API — из работающего config.json. Печатает задержку, мс.
probe() {
    python3 - "$CONFIG" "$HEALTH_GROUP" "$HEALTH_URL" <<'PYEOF'
import json, sys, urllib.parse, urllib.request
cfg, group, url = sys.argv[1:4]
api = json.load(open(cfg, encoding="utf-8")).get("experimental", {}).get("clash_api", {})
ctl, secret = api.get("external_controller"), api.get("secret", "")
if not ctl:
    sys.exit(2)
q = urllib.parse.urlencode({"url": url, "timeout": 5000})
req = urllib.request.Request(f"http://{ctl}/proxies/{urllib.parse.quote(group)}/delay?{q}",
                             headers={"Authorization": f"Bearer {secret}"} if secret else {})
try:
    with urllib.request.urlopen(req, timeout=8) as r:
        delay = json.load(r).get("delay")
except Exception:
    sys.exit(1)
print(delay)
sys.exit(0 if delay else 1)
PYEOF
}

# Ждать связности до $HEALTH_WAIT с (urltest-группам нужно время на первый замер).
healthy() {
    waited=0
    while [ "$waited" -lt "$HEALTH_WAIT" ]; do
        if pidof sing-box >/dev/null && d="$(probe)"; then
            log "связность есть: $HEALTH_GROUP → $HEALTH_URL за ${d} мс"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
    done
    log "связности нет $HEALTH_WAIT с (группа $HEALTH_GROUP)"
    return 1
}

if [ "$HEALTH_ONLY" = 1 ]; then
    healthy
    exit $?
fi

[ -n "$NODES" ] || die "не указан nodes.json"
[ -f "$NODES" ] || die "нет файла: $NODES"
[ -f "$BASE" ] || die "нет базы: $BASE"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

sing-box merge "$TMP" -c "$BASE" -c "$NODES" >/dev/null 2>&1 \
    || die "sing-box merge не удался — работающий конфиг не тронут"
sing-box check -c "$TMP" || die "итоговый конфиг не прошёл sing-box check — работающий конфиг не тронут"

# Сводка: какие теги outbounds/endpoints появятся и исчезнут.
python3 - "$CONFIG" "$TMP" <<'PYEOF'
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
PYEOF

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
log "перезапуск sing-box ($REASON)…"
/etc/init.d/sing-box restart
if healthy; then
    rm -f "$RULES_MARK"
    log "применено; прежний конфиг: $CONFIG.prev"
    exit 0
fi

log "новый конфиг не дал связности — возвращаю прежний и перезапускаю"
cp -p "$CONFIG" "$CONFIG.failed"
cp -p "$CONFIG.prev" "$CONFIG"
/etc/init.d/sing-box restart
if healthy; then
    die "откатились на прежний config.json, связность есть (неудачный — $CONFIG.failed)"
fi
die "связности нет и на прежнем конфиге — нужна ручная проверка (ssh по LAN работает без sing-box)"
