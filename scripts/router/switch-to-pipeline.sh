#!/bin/sh
#
# switch-to-pipeline.sh — однократное переключение роутера со старой генерации
# (update-singbox-config.sh / update-clients-configs.sh из *.old) на новый конвейер pipeline.sh.
#
#   switch-to-pipeline.sh [--dry-run]
#
# Сам уходит в фон (setsid): во время рестарта sing-box сеть пропадает — вместе с ней может
# оборваться SSH-сессия, запустившая скрипт; переключение от этого не прерывается.
# Ход — в $LOG. Шаги:
#   1. снимок состояния «до переключения» → /root/backups/nodes-tester-preswitch (для отката:
#      rollback-nodes-tester.sh -y /root/backups/nodes-tester-preswitch);
#   2. nodes_config migrate --force: свежие v1-конфиги → $DATA/config-main, $DATA/config-wh;
#   3. pipeline.sh router — боевой прогон: nodes.json → правила → apply-nodes (рестарт sing-box,
#      проверка связности, при провале — автоматический возврат прежнего config.json);
#   4. pipeline.sh clients — whnodes.json и клиентские конфиги;
#   5. ТОЛЬКО если шаг 3 успешен — cron: 03:00 pipeline.sh router, :07 pipeline.sh clients,
#      теневые строки и старые скрипты убираются. Иначе cron остаётся старым.
#
# --dry-run: без снимка и cron, конвейеры в --dry-run (проверка самого сценария).

set -u

PROJECT_DIR="${PROJECT_DIR:-/root/sing-box-nodes_tester}"
DATA="${DATA:-/root/nodes-data}"
HERE="$PROJECT_DIR/scripts/router"
LOG="$DATA/switch.log"
PIPELOG="/var/log/nodes-pipeline.log"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

if [ "${NODES_SWITCH_CHILD:-}" != 1 ]; then
    mkdir -p "$DATA"
    NODES_SWITCH_CHILD=1 setsid sh "$0" $DRY </dev/null >>"$LOG" 2>&1 &
    echo "переключение запущено в фоне${DRY:+ (dry-run)}; ход: tail -f $LOG"
    exit 0
fi

log() { echo "[switch] $(date '+%F %T') $*"; logger -t nodes-switch "$*" 2>/dev/null || true; }

log "===== старт${DRY:+ (dry-run)} ====="
cd "$PROJECT_DIR" || { log "нет $PROJECT_DIR"; exit 1; }

if [ -z "$DRY" ]; then
    snap="$(/root/backups/backup-nodes-tester.sh | sed -n 's/^Снимок готов: \([^ ]*\).*/\1/p')"
    [ -d "$snap" ] || { log "снимок не создан — стоп, ничего не менялось"; exit 1; }
    ln -sfn "$snap" /root/backups/nodes-tester-preswitch
    log "снимок: $snap (→ nodes-tester-preswitch)"
fi

python3 -m nodes_config migrate --from config --to "$DATA/config-main" --force \
    && python3 -m nodes_config migrate --from config_whitelist --to "$DATA/config-wh" --force \
    || { log "migrate не удался — стоп, боевое не менялось"; exit 1; }
log "конфиги v2 обновлены из текущих v1"

log "pipeline router…"
if ! "$HERE/pipeline.sh" router $DRY; then
    log "pipeline router НЕ удался — cron остаётся старым (apply-nodes сам вернул прежний конфиг sing-box)"
    exit 1
fi
log "pipeline router ок"

log "pipeline clients…"
"$HERE/pipeline.sh" clients $DRY || log "pipeline clients с ошибкой — клиентские конфиги прежние"

if [ -n "$DRY" ]; then
    log "===== dry-run завершён: cron не менялся ====="
    exit 0
fi

tmp="$(mktemp)"
crontab -l | grep -v -e 'update-singbox-config.sh' -e 'update-clients-configs.sh' \
                     -e 'shadow-pipeline.sh' -e 'теневой прогон' > "$tmp"
{
    echo "# конвейер nodes-tester (scripts/router/pipeline.sh; подробности — README.md)"
    echo "0 3 * * * $HERE/pipeline.sh router >> $PIPELOG 2>&1"
    echo "7 * * * * $HERE/pipeline.sh clients >> $PIPELOG 2>&1"
} >> "$tmp"
crontab "$tmp" && rm -f "$tmp"
log "cron переключён:"
crontab -l | sed 's/^/    /'
log "===== переключение завершено ====="
