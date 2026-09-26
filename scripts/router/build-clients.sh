#!/bin/sh
#
# build-clients.sh — собрать клиентские конфиги sing-box: base_<client>.json (репозиторий
# sing-box-config) + whnodes.json → /etc/sing-box-clients/<client>.json. Выделено из
# update-clients-configs.sh без изменения логики (сам whnodes.json теперь собирает конвейер
# nodes_fetch → nodes_config, см. pipeline.sh clients).
#
#   build-clients.sh [--dry-run] WHNODES_JSON
#
#   --dry-run  без git и без публикации: собрать в $DRY_OUT и сравнить с опубликованными
#
# Мерж — своей JSON-утилитой merge-configs.py, а не `sing-box merge`: тот переписывает конфиг
# в каноничной форме ВЕРСИИ РОУТЕРА (выкидывает дефолтные поля, меняет форму значений,
# переставляет outbounds), а клиенту нужен ровно JSON из репозитория. `sing-box check` не
# делаем по той же причине — валидирует sing-box на клиенте при импорте. Клиенты независимы:
# сбой одного не рвёт остальных, прошлый <client>.json остаётся.

set -u

REPO_DIR="${REPO_DIR:-/root/singbox-repo}"
CLIENTS_OUT="${CLIENTS_OUT:-/etc/sing-box-clients}"
DRY_OUT="${DRY_OUT:-/root/nodes-data/clients-dry}"
MERGE="$REPO_DIR/merge-configs.py"
# Клиенты: для каждого нужен android_clients/base_<name>.json в репозитории. Список — из
# $CLIENTS или файла $CLIENTS_FILE (не в git; шаблон — clients.list.example рядом).
CLIENTS_FILE="${CLIENTS_FILE:-$(dirname "$0")/clients.list}"
if [ -z "${CLIENTS:-}" ] && [ -f "$CLIENTS_FILE" ]; then
    CLIENTS="$(grep -v '^[[:space:]]*#' "$CLIENTS_FILE" | tr '\n' ' ')"
fi
CLIENTS="${CLIENTS:-}"
DRY=0; WHNODES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) WHNODES="$1" ;;
    esac
    shift
done

log() { echo "[clients] $*"; logger -t singbox-clients "$*" 2>/dev/null || true; }

[ -n "$WHNODES" ] && [ -f "$WHNODES" ] || { log "ОШИБКА: нет whnodes.json: $WHNODES"; exit 1; }
[ -n "$CLIENTS" ] || { log "ОШИБКА: список клиентов пуст (задай CLIENTS или $CLIENTS_FILE)"; exit 1; }

OUT="$CLIENTS_OUT"
if [ "$DRY" = 1 ]; then
    OUT="$DRY_OUT"
else
    cd "$REPO_DIR" && git fetch origin || log "git fetch не удался — беру файлы, что есть"
    git -C "$REPO_DIR" checkout origin/master -- merge-configs.py || true
    for name in $CLIENTS; do
        git -C "$REPO_DIR" checkout origin/master -- "android_clients/base_${name}.json" \
            || log "не удалось обновить base_${name}.json из репозитория"
    done
fi
mkdir -p "$OUT"

FAILED=0
for name in $CLIENTS; do
    base="$REPO_DIR/android_clients/base_${name}.json"
    out="$OUT/${name}.json"
    [ -f "$base" ] || { log "нет базы, пропуск: $base"; continue; }
    tmp="$(mktemp)"
    if python3 "$MERGE" "$tmp" "$base" "$WHNODES"; then
        mv "$tmp" "$out"
        if [ "$DRY" = 1 ]; then
            cmp -s "$out" "$CLIENTS_OUT/${name}.json" && log "$name: как опубликованный" \
                                                      || log "$name: отличается от опубликованного"
        else
            log "собран $out"
        fi
    else
        rm -f "$tmp"
        FAILED=1
        log "мерж не удался для '$name', прежний $out оставлен"
    fi
done

# work-hiddify.json — PC-клиент Hiddify: только outbounds (роутинг делает роутер по auth_user),
# WH-ноды не нужны — публикуется файл из репозитория как есть.
if [ "$DRY" = 0 ]; then
    if git -C "$REPO_DIR" checkout origin/master -- pc_clients/work-hiddify.json; then
        cp "$REPO_DIR/pc_clients/work-hiddify.json" "$CLIENTS_OUT/work-hiddify.json"
        log "опубликован $CLIENTS_OUT/work-hiddify.json"
    else
        log "не удалось обновить pc_clients/work-hiddify.json, прежний оставлен"
    fi
fi

exit "$FAILED"
