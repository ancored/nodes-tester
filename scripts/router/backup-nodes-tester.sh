#!/bin/sh
#
# backup-nodes-tester.sh — снимок РАБОТАЮЩЕЙ установки nodes-tester на роутере
# (код, конфиги, init.d, cron, скрипты генерации, nodes.json, конфиг sing-box, stats.db).
# Нужен как точка отката перед выкатом новой версии — см. rollback-nodes-tester.sh.
#
#   backup-nodes-tester.sh [--stable]
#
# Кладёт снимок в /root/backups/nodes-tester-<YYYYmmdd-HHMMSS>/. С --stable
# переставляет ссылку /root/backups/nodes-tester-stable на этот снимок (по ней
# откатывается rollback по умолчанию). Сервисы НЕ останавливает: stats.db
# снимается онлайн через sqlite backup API (консистентно при WAL).

set -eu

PROJECT_DIR="/root/sing-box-nodes_tester"
BACKUP_ROOT="/root/backups"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/nodes-tester-$TS"

# Всё, что входит в «работающую версию». Отсутствующие пути пропускаются.
PATHS="
$PROJECT_DIR
/etc/init.d/nodes-tester
/root/update-singbox-config.sh
/root/update-clients-configs.sh
/root/update-singbox-lx.sh
/etc/sing-box-subscribe
/etc/sing-box-clients
/etc/sing-box
"

mkdir -p "$DEST"
cd /

# --- 1. Файлы (БД — отдельно, консистентным снимком) -------------------------
LIST="$DEST/paths.txt"
: > "$LIST"
for p in $PATHS; do
    if [ -e "$p" ]; then
        echo "${p#/}" >> "$LIST"
    else
        echo "  (нет, пропуск) $p"
    fi
done
EXCL="$DEST/exclude.txt"
cat > "$EXCL" <<EOF
${PROJECT_DIR#/}/results/stats.db
${PROJECT_DIR#/}/results/stats.db-wal
${PROJECT_DIR#/}/results/stats.db-shm
EOF
find "${PROJECT_DIR#/}" -name __pycache__ -type d >> "$EXCL" 2>/dev/null || true
tar czf "$DEST/files.tar.gz" -X "$EXCL" -T "$LIST"

# --- 2. stats.db — онлайн-бэкап (тестер может писать в этот момент) ----------
DB="$PROJECT_DIR/results/stats.db"
if [ -f "$DB" ]; then
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
fi

# --- 3. Состояние системы ----------------------------------------------------
crontab -l > "$DEST/crontab.txt" 2>/dev/null || : > "$DEST/crontab.txt"
ls /etc/rc.d | grep -E 'nodes-|sing-box' > "$DEST/rc.d.txt" || true
pip3 list 2>/dev/null > "$DEST/pip.txt" || true
sing-box version 2>/dev/null | head -1 > "$DEST/sing-box-version.txt" || true
ls /etc/init.d | grep '^nodes-' > "$DEST/services.txt" || true

# Скрипт отката кладём в снимок: откат не зависит от того, что потом станет с репо.
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SELF_DIR/rollback-nodes-tester.sh" ]; then
    cp "$SELF_DIR/rollback-nodes-tester.sh" "$DEST/rollback.sh"
    chmod +x "$DEST/rollback.sh"
fi

( cd "$DEST" && sha256sum files.tar.gz stats.db.gz 2>/dev/null > SHA256SUMS ) || true

{
    echo "created:  $(date -Iseconds)"
    echo "host:     $(uname -n)"
    echo "services: $(tr '\n' ' ' < "$DEST/services.txt")"
    echo "enabled:  $(tr '\n' ' ' < "$DEST/rc.d.txt")"
    echo "sing-box: $(cat "$DEST/sing-box-version.txt")"
} > "$DEST/MANIFEST"

if [ "${1:-}" = "--stable" ]; then
    ln -sfn "$DEST" "$BACKUP_ROOT/nodes-tester-stable"
    echo "  -> $BACKUP_ROOT/nodes-tester-stable"
fi

echo "Снимок готов: $DEST ($(du -sh "$DEST" | cut -f1))"
cat "$DEST/MANIFEST"
