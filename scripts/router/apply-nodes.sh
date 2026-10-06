#!/bin/sh
#
# apply-nodes.sh — применить nodes.json к sing-box роутера: склейка base.json + nodes.json +
# пресеты (nodes_admin.presets) → sing-box check → замена config.json → рестарт.
#
#   apply-nodes.sh [--dry-run] [--force] NODES_JSON
#   apply-nodes.sh --health          только проверить связность работающего sing-box
#
#   --dry-run  всё то же (склейка + check + diff), но ничего не заменяет и не перезапускает;
#              кандидат кладётся в $CANDIDATE для просмотра
#   --force    перезапустить, даже если итоговый конфиг не изменился
#
#   BASE=...         база (по умолчанию $TARGET_DIR/base.json)
#   PRESETS_DIR=...  каталог пресетов правил: включённые склеиваются после базы и нод по приоритету
#
# sing-box перезапускается ТОЛЬКО если итоговый config.json изменился или задан --force.
# Новая база при том же итоговом конфиге перезапуска не требует. Изменённые .srs и
# source-наборы sing-box перечитывает сам, без перезапуска. Невалидный конфиг
# (склейка/check упали) — работающий config.json не трогается, exit 1.
#
# После рестарта — проверка РЕАЛЬНОЙ связности (до $HEALTH_WAIT с): через API-сервис sing-box
# запускается URL-тест боевой группы $HEALTH_GROUP (адрес проверки — из настроек urltest). Не прошла (процесс не
# поднялся или трафик не ходит) → автоматически возвращается прежний config.json и sing-box
# перезапускается на нём. Пока sing-box перезапускается, сеть (и удалённая сессия) может
# пропасть — поэтому скрипт не зависит от того, кто его запустил; запускать его вручную
# стоит отвязанным от сессии: setsid sh -c 'trap "" HUP; exec …'.
#
# Коды выхода: 0 — применено или менять нечего; 1 — ошибка (боевой конфиг цел или возвращён).

set -u

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
TARGET_DIR="${TARGET_DIR:-/etc/sing-box}"
BASE="${BASE:-$TARGET_DIR/base.json}"
PRESETS_DIR="${PRESETS_DIR:-}"
CONFIG="$TARGET_DIR/config.json"
RULES_MARK="${RULES_MARK:-/tmp/nodes-rules-changed}"
CANDIDATE="${CANDIDATE:-${DATA:-/opt/nodes-tester}/config.candidate.json}"
HEALTH_GROUP="${HEALTH_GROUP:-global-auto-out}"
HEALTH_WAIT="${HEALTH_WAIT:-60}"
# Флаг «sing-box остановлен из админки» (nodes_tester.singbox_ctl): пока он есть, конфиг
# не применяется и sing-box не поднимается.
STOP_FLAG="${STOP_FLAG:-/var/run/nodes-tester/singbox-stopped}"
DRY=0; FORCE=0; HEALTH_ONLY=0; NODES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --force) FORCE=1 ;;
        --health) HEALTH_ONLY=1 ;;
        -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) NODES="$1" ;;
    esac
    shift
done

log() { echo "[apply] $(date '+%F %T') $*"; logger -t nodes-apply "$*" 2>/dev/null || true; }
die() { log "ОШИБКА: $*"; exit 1; }

# Один запрос проверки: API-сервис sing-box отвечает, и URL-тест группы $HEALTH_GROUP
# реально проходит до ноды. Адрес и секрет API — из services[type=api] работающего
# config.json. Печатает задержку, мс.
probe() {
    PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m nodes_common.box_api health --config "$CONFIG" --group "$HEALTH_GROUP" 2>/dev/null
}

# Ждать связности до $HEALTH_WAIT с (urltest-группам нужно время на первый замер).
healthy() {
    waited=0
    while [ "$waited" -lt "$HEALTH_WAIT" ]; do
        if pidof sing-box >/dev/null && d="$(probe)"; then
            log "связность есть: URL-тест $HEALTH_GROUP за ${d} мс"
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

PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m nodes_admin.presets assemble     --base "$BASE" --nodes "$NODES" ${PRESETS_DIR:+--dir "$PRESETS_DIR"} --out "$TMP"     || die "склейка конфига не удалась — работающий конфиг не тронут"
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
[ "$FORCE" = 1 ] && REASON="${REASON:+$REASON, }--force"

if [ -z "$REASON" ]; then
    # Итоговый конфиг включает базу целиком: если он тот же, новая база (например, правила,
    # вынесенные в пресеты) ничего не меняет — перезапуск не нужен.
    if [ -f "$RULES_MARK" ]; then
        log "база обновилась, но итоговый конфиг тот же — sing-box не трогаю"
        [ "$DRY" = 1 ] || rm -f "$RULES_MARK"
    else
        log "без изменений — sing-box не трогаю"
    fi
    exit 0
fi

if [ "$DRY" = 1 ]; then
    mkdir -p "$(dirname "$CANDIDATE")"
    cp "$TMP" "$CANDIDATE"
    log "DRY-RUN: применил бы ($REASON); кандидат: $CANDIDATE"
    exit 0
fi

if [ -f "$STOP_FLAG" ]; then
    die "sing-box остановлен из админки — применение отложено (запустите sing-box в «Нодах»)"
fi

# Do not abandon the replacement/health-check/rollback sequence on timeout.
trap '' TERM INT HUP
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
