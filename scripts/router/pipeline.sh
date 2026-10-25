#!/bin/sh
#
# pipeline.sh — конвейер на роутере (до оркестратора Ф5 его зовёт cron):
#
#   pipeline.sh router  [--dry-run]   подписки → nodes.json → правила → применение к sing-box
#   pipeline.sh clients [--dry-run]   проверенные тестером ноды nodes.json → whnodes.json →
#                                     клиентские конфиги (подписки не загружаются)
#   pipeline.sh fetch   [--dry-run]   только опрос подписок → raw/router.json
#                                     (без сборки и применения; расписание — pipeline.fetch.every)
#   pipeline.sh apply   [--dry-run]   база + пресеты правил → применение к текущему nodes.json
#                                     (подписки не загружаются)
#   pipeline.sh apply-clients [--dry-run]   базы клиентов + клиентские пресеты → клиентские
#                                     конфиги по текущему whnodes.json (подписки не загружаются)
#
# router:  nodes_fetch (все подписки) → nodes_config → $DATA/nodes.json →
#          update-rules.sh → apply-nodes.sh (рестарт только при изменениях); ноды подписок
#          только для клиентов — в селекторе тестера, не в группах роутера
# clients: nodes_config --branch clients ($DATA/nodes.json + кандидаты клиентских групп от
#          тестера, $DATA/client-candidates.json) → $DATA/whnodes.json → build-clients.sh
#
# --dry-run: ничего боевого не трогает — nodes.json/whnodes.json пишутся в $DATA/*.dry.json,
# правила не обновляются, apply-nodes/build-clients работают в --dry-run.
# Настройки — $CFG_ROOT/config.json (подписки, фильтры, группы, клиенты).
# PROJECT_DIR по умолчанию — корень кода рядом со скриптом.
# guard nodes_fetch (exit 2) не прерывает конвейер: raw остаётся прошлым, сборка идёт по нему.
# Пресеты правил — $SINGBOX/presets/*.json (включённые склеиваются после нод по приоритету),
# клиентские — $SINGBOX/clients/presets/*.json (склеивает build-clients.sh);
# --dry-run проверяет редактируемую базу $SINGBOX/base.json, а не применённую копию.

set -u

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
DATA="${DATA:-/opt/nodes-tester}"
CFG_ROOT="${CFG_ROOT:-/etc/nodes-tester}"   # где лежит config.json
export DATA
HERE="$PROJECT_DIR/scripts/router"
. "$HERE/pipeline-lock.sh"
pipeline_exit() {
    pipeline_status=$?
    pipeline_lock_release
    if [ -n "${PIPELINE_EXIT_FILE:-}" ]; then
        pipeline_exit_tmp="$PIPELINE_EXIT_FILE.$$.tmp"
        if printf '%s\n' "$pipeline_status" > "$pipeline_exit_tmp"; then
            mv -f "$pipeline_exit_tmp" "$PIPELINE_EXIT_FILE"
        else
            rm -f "$pipeline_exit_tmp"
        fi
    fi
}
trap 'pipeline_exit' 0
MODE="${1:-}"; DRY=0
[ $# -le 2 ] || { echo "слишком много аргументов" >&2; exit 2; }
[ $# -lt 2 ] || [ "$2" = "--dry-run" ] || { echo "неизвестный аргумент: $2" >&2; exit 2; }
[ "${2:-}" = "--dry-run" ] && DRY=1

log() { echo "[pipeline] $*"; logger -t nodes-pipeline "$*" 2>/dev/null || true; }

case "$MODE" in
    router|apply|fetch) OUT="$DATA/nodes.json" ;;
    clients|apply-clients) OUT="$DATA/whnodes.json" ;;
    *) echo "использование: pipeline.sh router|clients|fetch|apply|apply-clients [--dry-run]" >&2; exit 2 ;;
esac
SINGBOX="${SINGBOX:-$CFG_ROOT/singbox}"
export PRESETS_DIR="$SINGBOX/presets"
pipeline_lock_acquire || exit $?

if [ "$MODE" = apply ]; then
    [ -f "$OUT" ] || { log "нет $OUT — сначала соберите конфиг (pipeline router)"; exit 1; }
    if [ "$DRY" = 1 ]; then
        BASE="$SINGBOX/base.json" "$HERE/apply-nodes.sh" --dry-run "$OUT"
    else
        "$HERE/update-rules.sh" --base-only || { log "база не скопирована"; exit 1; }
        "$HERE/apply-nodes.sh" "$OUT"
    fi
    exit $?
fi
if [ "$MODE" = apply-clients ]; then
    [ -f "$OUT" ] || { log "нет $OUT — сначала соберите клиентов (pipeline clients)"; exit 1; }
    if [ "$DRY" = 1 ]; then
        "$HERE/build-clients.sh" --dry-run "$OUT"
    else
        "$HERE/build-clients.sh" "$OUT"
    fi
    exit $?
fi
CONFIG="$CFG_ROOT/config.json"
[ "$DRY" = 1 ] && OUT="$DATA/$(basename "$OUT" .json).dry.json"
[ -f "$CONFIG" ] || { log "нет $CONFIG"; exit 1; }

cd "$PROJECT_DIR" || exit 1
mkdir -p "$DATA/raw"

if [ "$MODE" = fetch ]; then
    if [ "$DRY" = 1 ]; then
        python3 -m nodes_fetch -c "$CONFIG" --dry-run
    else
        python3 -m nodes_fetch -c "$CONFIG" -o "$DATA/raw/router.json"
    fi
    code=$?
    case "$code" in
        0) exit 0 ;;
        2) log "fetch: guard — прошлый raw сохранён"; exit 0 ;;
        *) log "fetch: ошибка ($code)"; exit 1 ;;
    esac
fi

if [ "$MODE" = clients ]; then
    [ -f "$DATA/nodes.json" ] || { log "нет $DATA/nodes.json — сначала соберите роутер (pipeline router)"; exit 1; }
    python3 -m nodes_config -c "$CONFIG" --branch clients --nodes "$DATA/nodes.json" \
        --candidates "$DATA/client-candidates.json" -o "$OUT" \
        || { log "config: ошибка — $OUT и конфиги клиентов не тронуты"; exit 1; }
    if [ "$DRY" = 1 ]; then
        "$HERE/build-clients.sh" --dry-run "$OUT"
    else
        "$HERE/build-clients.sh" "$OUT"
    fi
    exit $?
fi

python3 -m nodes_fetch -c "$CONFIG" -o "$DATA/raw/router.json"
code=$?
case "$code" in
    0) ;;
    2) log "fetch: guard — работаем на прошлом raw" ;;
    *) log "fetch: ошибка ($code) — конвейер остановлен, боевые файлы не тронуты"; exit 1 ;;
esac

python3 -m nodes_config -c "$CONFIG" --raw "$DATA/raw/router.json" -o "$OUT" \
    || { log "config: ошибка — $OUT не тронут"; exit 1; }

if [ "$DRY" = 1 ]; then
    BASE="$SINGBOX/base.json" "$HERE/apply-nodes.sh" --dry-run "$OUT"
else
    "$HERE/update-rules.sh" || { log "обновление правил не удалось"; exit 1; }
    "$HERE/apply-nodes.sh" "$OUT"
fi
