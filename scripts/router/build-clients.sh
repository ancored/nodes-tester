#!/bin/sh
# Сборка клиентов: base_<имя>.json + whnodes.json + включённые clients/presets/*.json,
# проверка sing-box check, публикация в $CLIENTS_OUT; файлы clients/publish/ копируются как есть.
set -u

CONFIG_DIR="${CONFIG_DIR:-/etc/nodes-tester}"
SOURCE="$CONFIG_DIR/singbox/clients"
SINGBOX_BIN="${SINGBOX_BIN:-sing-box}"
CLIENTS_OUT="${CLIENTS_OUT:-/etc/sing-box-clients}"
DRY_OUT="${DRY_OUT:-${DATA:-/opt/nodes-tester}/clients-dry}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
CLIENTS_FILE="${CLIENTS_FILE:-$CONFIG_DIR/clients.list}"
if [ -z "${CLIENTS:-}" ] && [ -f "$CLIENTS_FILE" ]; then
    CLIENTS="$(sed 's/#.*//' "$CLIENTS_FILE" | tr '\n' ' ')"
fi
CLIENTS="${CLIENTS:-}"
DRY=0
WHNODES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) [ -z "$WHNODES" ] || { echo "ожидается один whnodes.json" >&2; exit 2; }
           WHNODES="$1" ;;
    esac
    shift
done

log() { echo "[clients] $*"; logger -t singbox-clients "$*" 2>/dev/null || true; }
[ -n "$WHNODES" ] && [ -f "$WHNODES" ] || { log "нет whnodes.json: $WHNODES"; exit 1; }
[ -n "$CLIENTS" ] || { log "список клиентов пуст (задай CLIENTS или $CLIENTS_FILE)"; exit 1; }

OUT="$CLIENTS_OUT"
[ "$DRY" = 1 ] && OUT="$DRY_OUT"
mkdir -p "$OUT"
FAILED=0
for name in $CLIENTS; do
    case "$name" in
        *[!A-Za-z0-9_-]*|'') log "недопустимое имя клиента: $name"; FAILED=1; continue ;;
    esac
    base="$SOURCE/base_${name}.json"
    out="$OUT/${name}.json"
    [ -f "$base" ] || { log "нет базы, пропуск: $base"; FAILED=1; continue; }
    tmp="$(mktemp "$OUT/.${name}.XXXXXX")" || { FAILED=1; continue; }
    if PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m nodes_admin.presets assemble \
            --base "$base" --nodes "$WHNODES" --dir "$SOURCE/presets" \
            --client-settings "$SOURCE/settings.json" --out "$tmp"; then
        if command -v "$SINGBOX_BIN" >/dev/null 2>&1 && ! check="$("$SINGBOX_BIN" check -c "$tmp" 2>&1)"; then
            rm -f "$tmp"
            FAILED=1
            log "sing-box check отклонил '$name', прежний $out оставлен: $check"
            continue
        fi
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
        log "склейка не удалась для '$name', прежний $out оставлен"
    fi
done

if [ "$DRY" = 0 ] && [ -d "$SOURCE/publish" ]; then
    for src in "$SOURCE"/publish/*; do
        [ -f "$src" ] || continue
        name="$(basename "$src")"
        case "$name" in
            *[!A-Za-z0-9._-]*|.|..) log "недопустимое имя публикуемого файла: $name"; FAILED=1; continue ;;
        esac
        dest="$CLIENTS_OUT/$name"
        if ! cmp -s "$src" "$dest"; then
            case "$name" in
                *.json) if command -v "$SINGBOX_BIN" >/dev/null 2>&1 && ! check="$("$SINGBOX_BIN" check -c "$src" 2>&1)"; then
                            FAILED=1
                            log "sing-box check отклонил $src, прежний $dest оставлен: $check"
                            continue
                        fi ;;
            esac
            tmp="$(mktemp "$CLIENTS_OUT/.${name}.XXXXXX")" || { FAILED=1; continue; }
            if cp "$src" "$tmp" && mv "$tmp" "$dest"; then
                log "опубликован $dest"
            else
                rm -f "$tmp"
                FAILED=1
            fi
        fi
    done
fi
exit "$FAILED"
