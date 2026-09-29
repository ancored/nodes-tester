#!/bin/sh
#
# pipeline.sh — конвейер на роутере (до оркестратора Ф5 его зовёт cron):
#
#   pipeline.sh router  [--dry-run]   подписки → nodes.json → правила → применение к sing-box
#   pipeline.sh clients [--dry-run]   WH-подписки → whnodes.json → клиентские конфиги
#
# router:  nodes_fetch (main) → nodes_config → /etc/sing-box-subscribe/nodes.json →
#          update-rules.sh → apply-nodes.sh (рестарт только при изменениях)
# clients: nodes_fetch (wh)   → nodes_config → /etc/sing-box-subscribe/whnodes.json →
#          build-clients.sh
#
# --dry-run: ничего боевого не трогает — nodes.json/whnodes.json пишутся в $DATA/*.dry.json,
# правила не обновляются, apply-nodes/build-clients работают в --dry-run.
# Конфиги v2 — $CFG_ROOT/config-main, $CFG_ROOT/config-wh (из `nodes_config migrate`);
# CFG_ROOT по умолчанию = $DATA. PROJECT_DIR по умолчанию — корень кода рядом со скриптом.
# guard nodes_fetch (exit 2) не прерывает конвейер: raw остаётся прошлым, сборка идёт по нему.

set -u

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
DATA="${DATA:-/root/nodes-data}"
CFG_ROOT="${CFG_ROOT:-$DATA}"   # где лежат config-main/ и config-wh/
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
    router)  SET=main; OUT=/etc/sing-box-subscribe/nodes.json ;;
    clients) SET=wh;   OUT=/etc/sing-box-subscribe/whnodes.json ;;
    *) echo "использование: pipeline.sh router|clients [--dry-run]" >&2; exit 2 ;;
esac
pipeline_lock_acquire || exit $?
CFG="$CFG_ROOT/config-$SET"
[ "$DRY" = 1 ] && OUT="$DATA/$(basename "$OUT" .json).dry.json"
[ -f "$CFG/providers.json" ] || { log "нет $CFG/providers.json (сделай nodes_config migrate)"; exit 1; }

cd "$PROJECT_DIR" || exit 1
mkdir -p "$DATA/raw"

python3 -m nodes_fetch -p "$CFG/providers.json" -o "$DATA/raw/$SET.json"
code=$?
case "$code" in
    0) ;;
    2) log "fetch: guard — работаем на прошлом raw" ;;
    *) log "fetch: ошибка ($code) — конвейер остановлен, боевые файлы не тронуты"; exit 1 ;;
esac

UN=""
[ -f "$CFG/user_nodes.json" ] && UN="--user-nodes $CFG/user_nodes.json"
python3 -m nodes_config --raw "$DATA/raw/$SET.json" --groups "$CFG/groups_params.json" $UN -o "$OUT" \
    || { log "config: ошибка — $OUT не тронут"; exit 1; }

if [ "$MODE" = router ]; then
    if [ "$DRY" = 1 ]; then
        "$HERE/apply-nodes.sh" --dry-run "$OUT"
    else
        "$HERE/update-rules.sh" || { log "обновление правил не удалось"; exit 1; }
        "$HERE/apply-nodes.sh" "$OUT"
    fi
else
    if [ "$DRY" = 1 ]; then
        "$HERE/build-clients.sh" --dry-run "$OUT"
    else
        "$HERE/build-clients.sh" "$OUT"
    fi
fi
