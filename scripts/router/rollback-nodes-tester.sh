#!/bin/sh
#
# rollback-nodes-tester.sh — быстрый откат nodes-tester на снимок backup-nodes-tester.sh.
#
#   rollback-nodes-tester.sh [-y] [--keep-db] [--no-regen] [--check] [SNAPSHOT_DIR]
#
#   SNAPSHOT_DIR  каталог снимка (по умолчанию /root/backups/nodes-tester-stable)
#   -y            без подтверждения
#   --keep-db     оставить ТЕКУЩУЮ stats.db (по умолчанию восстанавливается снимок БД —
#                 новая версия могла мигрировать схему; текущая БД всё равно сохраняется)
#   --no-regen    не перегенерировать nodes.json старым update-singbox-config.sh после отката
#                 (по умолчанию перегенерирует: ноды в снимке могут быть устаревшими).
#                 Если конфиг sing-box со снимка не менялся — sing-box не трогается вовсе
#                 (ни рестарта, ни перегенерации): откат только тестера/модулей сеть не рвёт.
#   --check       только проверить снимок (контрольные суммы, состав) и выйти
#
# Ничего не удаляет: текущее состояние (код, конфиги, БД, init.d, cron, /etc/sing-box)
# переносится в /root/backups/failed-<ts>/ — откат можно отменить вручную.

set -eu

PROJECT_DIR="/root/sing-box-nodes_tester"
SNAP="/root/backups/nodes-tester-stable"
YES=0; KEEP_DB=0; REGEN=1; CHECK_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        -y) YES=1 ;;
        --keep-db) KEEP_DB=1 ;;
        --no-regen) REGEN=0 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) SNAP="$1" ;;
    esac
    shift
done

SNAP="$(readlink -f "$SNAP")"
log() { echo "[rollback] $*"; logger -t nodes-rollback "$*" 2>/dev/null || true; }
die() { log "ОШИБКА: $*"; exit 1; }

# --- 0. Проверка снимка ------------------------------------------------------
[ -f "$SNAP/files.tar.gz" ] || die "нет снимка: $SNAP/files.tar.gz"
if [ -f "$SNAP/SHA256SUMS" ]; then
    ( cd "$SNAP" && sha256sum -c SHA256SUMS >/dev/null ) || die "контрольные суммы снимка не сошлись"
fi
tar tzf "$SNAP/files.tar.gz" >/dev/null || die "архив снимка повреждён"
log "снимок: $SNAP"
cat "$SNAP/MANIFEST" 2>/dev/null || true
[ "$CHECK_ONLY" = 1 ] && { log "снимок в порядке (--check)"; exit 0; }

if [ "$YES" != 1 ]; then
    printf "Откатить nodes-tester на этот снимок? [y/N] "
    read -r ans
    [ "$ans" = y ] || [ "$ans" = Y ] || { echo "отменено"; exit 1; }
fi

TS="$(date +%Y%m%d-%H%M%S)"
FAILED="/root/backups/failed-$TS"
mkdir -p "$FAILED/init.d"
log "текущее состояние → $FAILED"

# --- 1. Остановить ВСЕ nodes-* сервисы (включая новые, которых нет в снимке) --
for svc in /etc/init.d/nodes-*; do
    [ -f "$svc" ] || continue
    name="$(basename "$svc")"
    "$svc" stop 2>/dev/null || true
    cp -p "$svc" "$FAILED/init.d/"
    if ! grep -qx "$name" "$SNAP/services.txt" 2>/dev/null; then
        "$svc" disable 2>/dev/null || true
        rm -f "$svc"
        log "новый сервис $name остановлен, выключен и убран (копия в $FAILED/init.d)"
    fi
done
sleep 2
# страховка: процессы, пережившие stop (запущенные не через procd)
for pat in "python3 -u -m nodes_" "python3 -u -m dashboard" "python3 -m nodes_"; do
    pkill -f "$pat" 2>/dev/null || true
done

# --- 2. Сохранить текущее -----------------------------------------------------
crontab -l > "$FAILED/crontab.txt" 2>/dev/null || true
[ -d "$PROJECT_DIR" ] && mv "$PROJECT_DIR" "$FAILED/sing-box-nodes_tester"
for d in /etc/sing-box /etc/sing-box-subscribe /etc/sing-box-clients; do
    [ -d "$d" ] && cp -a "$d" "$FAILED/$(basename "$d")"
done
for f in /root/update-singbox-config.sh /root/update-clients-configs.sh /root/update-singbox-lx.sh; do
    [ -f "$f" ] && cp -p "$f" "$FAILED/"
done

# --- 3. Восстановить файлы ----------------------------------------------------
tar xzf "$SNAP/files.tar.gz" -C /
log "файлы восстановлены"

mkdir -p "$PROJECT_DIR/results"
if [ "$KEEP_DB" = 1 ] && [ -f "$FAILED/sing-box-nodes_tester/results/stats.db" ]; then
    for f in stats.db stats.db-wal stats.db-shm; do
        [ -f "$FAILED/sing-box-nodes_tester/results/$f" ] && \
            cp -p "$FAILED/sing-box-nodes_tester/results/$f" "$PROJECT_DIR/results/"
    done
    log "stats.db: оставлена текущая (--keep-db)"
elif [ -f "$SNAP/stats.db.gz" ]; then
    gunzip -c "$SNAP/stats.db.gz" > "$PROJECT_DIR/results/stats.db"
    log "stats.db: восстановлена из снимка (текущая — в $FAILED)"
fi

# --- 4. cron и автозапуск -----------------------------------------------------
[ -f "$SNAP/crontab.txt" ] && crontab "$SNAP/crontab.txt" && log "crontab восстановлен"
while read -r link; do
    name="${link#S[0-9][0-9]}"
    [ -f "/etc/init.d/$name" ] && "/etc/init.d/$name" enable
done < "$SNAP/rc.d.txt"

# --- 5. sing-box: конфиг из снимка, только если проходит check ----------------
# Если конфиг sing-box не менялся с момента снимка (откатываем только тестер/модули) —
# sing-box не трогаем вовсе: ни рестарта, ни перегенерации (сеть не рвётся).
if [ -f "$FAILED/sing-box/config.json" ] && \
   [ "$(sha256sum < "$FAILED/sing-box/config.json")" = "$(sha256sum < /etc/sing-box/config.json)" ]; then
    REGEN=0
    log "конфиг sing-box не менялся со снимка — sing-box не перезапускаю, перегенерацию пропускаю"
elif sing-box check -c /etc/sing-box/config.json; then
    /etc/init.d/sing-box restart
    log "sing-box перезапущен на конфиге из снимка"
else
    log "конфиг sing-box из снимка НЕ прошёл check — возвращаю текущий, sing-box не трогаю"
    rm -rf /etc/sing-box && cp -a "$FAILED/sing-box" /etc/sing-box
fi

# --- 6. Запуск старых сервисов ------------------------------------------------
for svc in $(cat "$SNAP/services.txt"); do
    [ -f "/etc/init.d/$svc" ] && "/etc/init.d/$svc" start
done
sleep 5
if pgrep -f "python3 -u -m nodes_tester" >/dev/null; then
    log "nodes-tester запущен"
else
    log "ВНИМАНИЕ: nodes-tester не поднялся — смотри: logread -e nodes-tester"
fi

# --- 7. Свежие ноды старым генератором (сам оставляет рабочий конфиг при сбое) --
if [ "$REGEN" = 1 ] && [ -x /root/update-singbox-config.sh ]; then
    log "перегенерация nodes.json старым update-singbox-config.sh…"
    /root/update-singbox-config.sh || log "перегенерация не удалась — работаем на нодах из снимка"
fi

log "откат завершён. Текущее (новое) состояние сохранено в $FAILED"
