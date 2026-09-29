#!/bin/sh
#
# rollback-nodes-tester.sh — откат пакета nodes-tester на снимок backup-nodes-tester.sh.
#
#   nodes-tester rollback [-y] [--keep-db] [--check] [SNAPSHOT_DIR]
#
#   SNAPSHOT_DIR  каталог снимка (по умолчанию /root/backups/nodes-tester-stable)
#   -y            без подтверждения
#   --keep-db     оставить ТЕКУЩИЕ stats.db и pipeline.db
#   --check       только проверить снимок (контрольные суммы, архивы) и выйти
#
# Пакет переустанавливается из файла в снимке; без него код возвращается из code.tar.gz
# (пакетный менеджер тогда продолжит показывать новую версию). Конфиг sing-box
# возвращается, только если изменился со снимка и проходит sing-box check.
# Ничего не удаляет: текущее состояние сохраняется в /root/backups/failed-<ts>/.

set -eu

SNAP="/root/backups/nodes-tester-stable"
YES=0; KEEP_DB=0; CHECK_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        -y) YES=1 ;;
        --keep-db) KEEP_DB=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '3,16p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) SNAP="$1" ;;
    esac
    shift
done

SNAP="$(readlink -f "$SNAP")"
log() { echo "[rollback] $*"; logger -t nodes-rollback "$*" 2>/dev/null || true; }
die() { log "ОШИБКА: $*"; exit 1; }

uci_get() { uci -q get "nodes-tester.$1" 2>/dev/null || echo "$2"; }
if command -v apk >/dev/null 2>&1; then
    EXT=apk
    installed() { apk info -v 2>/dev/null | sed -n 's/^nodes-tester-\([0-9].*\)$/\1/p' | head -1; }
    reinstall() { apk add --allow-untrusted "$1"; }
else
    EXT=ipk
    installed() { opkg status nodes-tester 2>/dev/null | sed -n 's/^Version: //p'; }
    reinstall() { opkg install --force-downgrade --force-reinstall "$1"; }
fi

# --- 0. Проверка снимка ------------------------------------------------------
for f in files.tar.gz code.tar.gz version.txt; do
    [ -f "$SNAP/$f" ] || die "в снимке нет $f: $SNAP"
done
if [ -s "$SNAP/SHA256SUMS" ]; then
    ( cd "$SNAP" && sha256sum -c SHA256SUMS >/dev/null ) || die "контрольные суммы снимка не сошлись"
fi
tar tzf "$SNAP/files.tar.gz" >/dev/null || die "архив files.tar.gz повреждён"
tar tzf "$SNAP/code.tar.gz" >/dev/null || die "архив code.tar.gz повреждён"
TARGET="$(cat "$SNAP/version.txt")"
PKG="$(ls "$SNAP"/nodes-tester-*."$EXT" 2>/dev/null | head -1 || true)"
log "снимок: $SNAP"
cat "$SNAP/MANIFEST" 2>/dev/null || true
if [ -n "$PKG" ]; then SOURCE="пакет $(basename "$PKG")"; else SOURCE="код из code.tar.gz"; fi
log "установлено: $(installed); откат на: $TARGET ($SOURCE)"
[ "$CHECK_ONLY" = 1 ] && { log "снимок в порядке (--check)"; exit 0; }

if [ "$YES" != 1 ]; then
    printf "Откатить nodes-tester на этот снимок? [y/N] "
    read -r ans
    [ "$ans" = y ] || [ "$ans" = Y ] || { echo "отменено"; exit 1; }
fi

CONFIG_DIR="$(uci_get main.config_dir /etc/nodes-tester)"
DATA_DIR="$(uci_get main.data_dir /opt/nodes-tester)"
TS="$(date +%Y%m%d-%H%M%S)"
FAILED="/root/backups/failed-$TS"
mkdir -p "$FAILED"

# --- 1. Остановить сервис и сохранить текущее состояние -----------------------
/etc/init.d/nodes-tester stop 2>/dev/null || true
sleep 2
log "текущее состояние → $FAILED"
cd /
CUR=""
for p in "$CONFIG_DIR" /etc/config/nodes-tester "$DATA_DIR" /etc/sing-box; do
    [ -e "$p" ] && CUR="$CUR ${p#/}"
done
# shellcheck disable=SC2086
tar czf "$FAILED/files.tar.gz" $CUR
crontab -l > "$FAILED/crontab.txt" 2>/dev/null || true
installed > "$FAILED/version.txt"

# --- 2. Пакет или код ---------------------------------------------------------
if [ -n "$PKG" ]; then
    reinstall "$PKG" || die "не удалось установить $(basename "$PKG"); текущее — в $FAILED"
    [ "$(installed)" = "$TARGET" ] || die "после установки версия $(installed), ожидалась $TARGET"
    log "пакет nodes-tester $TARGET установлен"
else
    tar xzf "$SNAP/code.tar.gz" -C /
    log "код $TARGET возвращён из code.tar.gz (пакетный менеджер показывает $(installed))"
fi

# --- 3. Рабочие файлы и БД ----------------------------------------------------
# sing-box разбирается отдельно (шаг 5): его конфиг распаковываем во временный каталог.
SB_TMP="$FAILED/snapshot-sing-box"
mkdir -p "$SB_TMP"
printf 'etc/sing-box\netc/sing-box/*\n' > "$FAILED/exclude.txt"   # busybox tar: только -X
tar xzf "$SNAP/files.tar.gz" -C / -X "$FAILED/exclude.txt"
tar xzf "$SNAP/files.tar.gz" -C "$SB_TMP" etc/sing-box 2>/dev/null || true
log "конфиги и данные восстановлены"

DB="$(cat "$SNAP/db_path.txt" 2>/dev/null || true)"
if [ -n "$DB" ] && [ -f "$SNAP/stats.db.gz" ] && [ "$KEEP_DB" != 1 ]; then
    mkdir -p "$(dirname "$DB")"
    for f in "$DB" "$DB-wal" "$DB-shm"; do
        [ -f "$f" ] && mv "$f" "$FAILED/"
    done
    gunzip -c "$SNAP/stats.db.gz" > "$DB"
    log "stats.db восстановлена из снимка (текущая — в $FAILED)"
else
    log "stats.db: оставлена текущая"
fi

PIPELINE_DB="$DATA_DIR/pipeline.db"
if [ -f "$SNAP/pipeline.db.gz" ] && [ "$KEEP_DB" != 1 ]; then
    mkdir -p "$DATA_DIR"
    for f in "$PIPELINE_DB" "$PIPELINE_DB-wal" "$PIPELINE_DB-shm"; do
        [ -f "$f" ] && mv "$f" "$FAILED/"
    done
    gunzip -c "$SNAP/pipeline.db.gz" > "$PIPELINE_DB"
    log "pipeline.db восстановлена из снимка"
fi

# --- 4. cron и автозапуск -----------------------------------------------------
if [ -f "$SNAP/crontab.txt" ]; then
    { crontab -l 2>/dev/null | grep -v 'nodes-tester' || true; cat "$SNAP/crontab.txt"; } > "$FAILED/crontab.new"
    crontab "$FAILED/crontab.new" && log "задачи nodes-tester в crontab восстановлены"
fi
if [ "$(cat "$SNAP/enabled.txt" 2>/dev/null)" = 1 ]; then
    /etc/init.d/nodes-tester enable
else
    /etc/init.d/nodes-tester disable 2>/dev/null || true
fi

# --- 5. sing-box: конфиг из снимка, только если изменился и проходит check ----
OLD_SB="$SB_TMP/etc/sing-box/config.json"
if [ ! -f "$OLD_SB" ] || [ ! -f /etc/sing-box/config.json ]; then
    log "в снимке нет конфига sing-box — sing-box не трогаю"
elif [ "$(sha256sum < "$OLD_SB")" = "$(sha256sum < /etc/sing-box/config.json)" ]; then
    log "конфиг sing-box не менялся со снимка — sing-box не перезапускаю"
elif sing-box check -c "$OLD_SB"; then
    cp -p /etc/sing-box/config.json "$FAILED/sing-box-config.json"
    cp -p "$OLD_SB" /etc/sing-box/config.json
    /etc/init.d/sing-box restart
    log "sing-box перезапущен на конфиге из снимка (текущий — в $FAILED)"
else
    log "конфиг sing-box из снимка НЕ прошёл check — оставляю текущий"
fi

# --- 6. Запуск ----------------------------------------------------------------
/etc/init.d/nodes-tester start || true
sleep 5
if /etc/init.d/nodes-tester status 2>/dev/null | grep -q running; then
    log "nodes-tester запущен"
else
    log "nodes-tester не запущен (выключен в UCI или ошибка) — смотри: logread -e nodes-tester"
fi
log "откат завершён. Состояние до отката сохранено в $FAILED"
