#!/bin/sh
#
# backup-nodes-tester.sh — снимок установленного пакета nodes-tester перед обновлением.
#
#   nodes-tester backup [--stable] [--pkg FILE]
#
#   --stable    перевесить ссылку /root/backups/nodes-tester-stable на этот снимок
#               (её по умолчанию берёт rollback-nodes-tester.sh)
#   --pkg FILE  положить в снимок файл установленного пакета (.apk/.ipk). Без ключа ищется
#               /root/packages/nodes-tester-<версия>.{apk,ipk}. Откат переустанавливает
#               пакет из снимка; если файла нет, возвращает код из code.tar.gz.
#
# Снимок: /root/backups/nodes-tester-<YYYYmmdd-HHMMSS>/
#   files.tar.gz   config_dir, /etc/config/nodes-tester, data_dir без БД, /etc/sing-box
#   code.tar.gz    файлы пакета (код, init.d, /usr/bin/nodes-tester)
#   stats.db.gz    онлайн-снимок SQLite backup API с integrity_check (сервис не останавливается)
#   crontab.txt, enabled.txt, MANIFEST, SHA256SUMS, rollback.sh, пакет (если найден)
#
# Пути config_dir и data_dir читаются из /etc/config/nodes-tester.

set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/root/backups}"
PKG_DIR="${PKG_DIR:-/root/packages}"
STABLE=0; PKG_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --stable) STABLE=1 ;;
        --pkg) [ $# -ge 2 ] || { echo "--pkg: нужен путь к файлу" >&2; exit 2; }
               PKG_FILE="$2"; shift ;;
        -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
    shift
done

uci_get() { uci -q get "nodes-tester.$1" 2>/dev/null || echo "$2"; }
CONFIG_DIR="$(uci_get main.config_dir /etc/nodes-tester)"
DATA_DIR="$(uci_get main.data_dir /opt/nodes-tester)"

# --- Пакетный менеджер: версия и список файлов -------------------------------
if command -v apk >/dev/null 2>&1; then
    PM=apk
    VERSION="$(apk info -v 2>/dev/null | sed -n 's/^nodes-tester-\([0-9].*\)$/\1/p' | head -1)"
    pkg_files() { apk info -L nodes-tester 2>/dev/null | grep -v ':$' | grep -v '^$'; }
    EXT=apk
else
    PM=opkg
    VERSION="$(opkg status nodes-tester 2>/dev/null | sed -n 's/^Version: //p')"
    pkg_files() { opkg files nodes-tester 2>/dev/null | tail -n +2 | sed 's#^/##'; }
    EXT=ipk
fi
[ -n "$VERSION" ] || { echo "пакет nodes-tester не установлен" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/nodes-tester-$TS"
mkdir -p "$DEST"
cd /
echo "Снимок nodes-tester $VERSION → $DEST"

# --- 1. Рабочие файлы (БД — отдельно, консистентным снимком) -----------------
LIST="$DEST/paths.txt"; : > "$LIST"
for p in "$CONFIG_DIR" /etc/config/nodes-tester "$DATA_DIR" /etc/sing-box; do
    if [ -e "$p" ]; then echo "${p#/}" >> "$LIST"; else echo "  (нет, пропуск) $p"; fi
done
EXCL="$DEST/exclude.txt"
for f in stats.db stats.db-wal stats.db-shm; do
    echo "${DATA_DIR#/}/results/$f"
done > "$EXCL"
tar czf "$DEST/files.tar.gz" -X "$EXCL" -T "$LIST"

# --- 2. Код пакета ------------------------------------------------------------
# Конфиги пакета уже в files.tar.gz; __pycache__ пакетный менеджер не знает.
# uci-defaults удаляется после первого запуска — берём только существующие файлы.
pkg_files | sed 's#^/##' | grep -v '^etc/nodes-tester/' | grep -v '^etc/config/' \
    | while read -r f; do [ -e "/$f" ] && echo "$f"; done > "$DEST/code.txt" || true
tar czf "$DEST/code.tar.gz" -T "$DEST/code.txt"

# --- 3. stats.db — онлайн-бэкап (тестер может писать в этот момент) ----------
DB="$(python3 - "$CONFIG_DIR/config.json" "$DATA_DIR" <<'EOF' 2>/dev/null || true
import json, os, sys
db = json.load(open(sys.argv[1], encoding="utf-8"))["storage"]["db_file"]
print(db if os.path.isabs(db) else os.path.normpath(os.path.join(sys.argv[2], db)))
EOF
)"
if [ -n "$DB" ] && [ -f "$DB" ]; then
    python3 - "$DB" "$DEST/stats.db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
print(f"  stats.db: integrity_check={ok}")
sys.exit(0 if ok == "ok" else 1)
EOF
    gzip "$DEST/stats.db"
    echo "$DB" > "$DEST/db_path.txt"
else
    echo "  (нет БД, пропуск) ${DB:-storage.db_file}"
fi

# --- 4. Пакет для отката ------------------------------------------------------
[ -n "$PKG_FILE" ] || PKG_FILE="$PKG_DIR/nodes-tester-$VERSION.$EXT"
if [ -f "$PKG_FILE" ]; then
    cp "$PKG_FILE" "$DEST/nodes-tester-$VERSION.$EXT"
    echo "  пакет: $(basename "$PKG_FILE")"
else
    echo "  (нет файла пакета $PKG_FILE — откат вернёт код из code.tar.gz)"
fi

# --- 5. Состояние системы ----------------------------------------------------
crontab -l 2>/dev/null | grep 'nodes-tester' > "$DEST/crontab.txt" || true
if /etc/init.d/nodes-tester enabled 2>/dev/null; then echo 1; else echo 0; fi > "$DEST/enabled.txt"
uci_get tester.enabled 0 > "$DEST/tester-enabled.txt"
sing-box version 2>/dev/null | head -1 > "$DEST/sing-box-version.txt" || true

# Скрипт отката кладём в снимок: откат не зависит от установленной версии.
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SELF_DIR/rollback-nodes-tester.sh" ]; then
    cp "$SELF_DIR/rollback-nodes-tester.sh" "$DEST/rollback.sh"
    chmod +x "$DEST/rollback.sh"
fi

( cd "$DEST" && sha256sum files.tar.gz code.tar.gz stats.db.gz nodes-tester-*."$EXT" \
    2>/dev/null > SHA256SUMS ) || true

{
    echo "created:    $(date -Iseconds)"
    echo "host:       $(uname -n)"
    echo "package:    nodes-tester $VERSION ($PM)"
    echo "config_dir: $CONFIG_DIR"
    echo "data_dir:   $DATA_DIR"
    echo "sing-box:   $(cat "$DEST/sing-box-version.txt")"
} > "$DEST/MANIFEST"
echo "$VERSION" > "$DEST/version.txt"

if [ "$STABLE" = 1 ]; then
    ln -sfn "$DEST" "$BACKUP_ROOT/nodes-tester-stable"
    echo "  -> $BACKUP_ROOT/nodes-tester-stable"
fi

echo "Снимок готов: $DEST ($(du -sh "$DEST" | cut -f1))"
cat "$DEST/MANIFEST"
