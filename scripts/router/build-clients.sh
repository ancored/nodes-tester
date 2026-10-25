#!/bin/sh
# Сборка клиентов из config.json (clients): base_<имя>.json + whnodes.json + включённые clients/presets/*.json
# или готовый clients/publish/<имя>.json, проверка sing-box check, публикация в $CLIENTS_OUT/<имя>.json и
# $CLIENTS_OUT/by-token/<токен>.json (их раздаёт веб-сервер). Ссылки отключённых и удалённых клиентов
# перестают работать: их файлы в by-token/ удаляются.
set -u

CONFIG_DIR="${CONFIG_DIR:-/etc/nodes-tester}"
SOURCE="$CONFIG_DIR/singbox/clients"
SINGBOX_BIN="${SINGBOX_BIN:-sing-box}"
DRY_OUT="${DRY_OUT:-${DATA:-/opt/nodes-tester}/clients-dry}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
DRY=0
WHNODES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
        -*) echo "неизвестный ключ: $1" >&2; exit 2 ;;
        *) [ -z "$WHNODES" ] || { echo "ожидается один whnodes.json" >&2; exit 2; }
           WHNODES="$1" ;;
    esac
    shift
done

log() { echo "[clients] $*"; logger -t singbox-clients "$*" 2>/dev/null || true; }
[ -n "$WHNODES" ] && [ -f "$WHNODES" ] || { log "нет whnodes.json: $WHNODES"; exit 1; }
CLIENTS_OUT="${CLIENTS_OUT:-$(python3 -m nodes_admin.clients -c "$CONFIG_DIR/config.json" output)}" \
    && [ -n "$CLIENTS_OUT" ] || { log "каталог публикации не прочитан из config.json"; exit 1; }
LIST="$(python3 -m nodes_admin.clients -c "$CONFIG_DIR/config.json" list)" \
    || { log "список клиентов не прочитан из config.json"; exit 1; }
[ -n "$LIST" ] || { log "клиентов нет (раздел clients в config.json)"; exit 1; }

OUT="$CLIENTS_OUT"
[ "$DRY" = 1 ] && OUT="$DRY_OUT"
TOKENS="$CLIENTS_OUT/by-token"
mkdir -p "$OUT"
[ "$DRY" = 1 ] || mkdir -p "$TOKENS"
FAILED=0
KEEP=" "

# Атомарно положить файл $1 в $2 (во временный файл рядом и mv).
publish() {
    tmp="$(mktemp "$(dirname "$2")/.publish.XXXXXX")" || return 1
    if cp "$1" "$tmp" && mv "$tmp" "$2"; then
        return 0
    fi
    rm -f "$tmp"
    return 1
}

while read -r name token; do
    [ -n "$name" ] || continue
    KEEP="$KEEP$token.json "
    base="$SOURCE/base_${name}.json"
    file="$SOURCE/publish/${name}.json"
    out="$OUT/${name}.json"
    tmp="$(mktemp "$OUT/.${name}.XXXXXX")" || { FAILED=1; continue; }
    if [ -f "$base" ]; then
        if ! python3 -m nodes_admin.presets assemble --base "$base" --nodes "$WHNODES" --dir "$SOURCE/presets" \
                --client-settings "$SOURCE/settings.json" --out "$tmp"; then
            rm -f "$tmp"
            FAILED=1
            log "склейка не удалась для '$name', прежний $out оставлен"
            continue
        fi
    elif [ -f "$file" ]; then
        cp "$file" "$tmp" || { rm -f "$tmp"; FAILED=1; continue; }
    else
        rm -f "$tmp"
        FAILED=1
        log "нет ни $base, ни $file — '$name' пропущен"
        continue
    fi
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
    elif publish "$out" "$TOKENS/$token.json"; then
        log "собран и опубликован $name"
    else
        FAILED=1
        log "не удалось опубликовать '$name' по токену"
    fi
done <<EOF
$LIST
EOF

if [ "$DRY" = 0 ]; then
    REVOKED=0
    for path in "$TOKENS"/*.json; do
        [ -f "$path" ] || continue
        case "$KEEP" in
            *" $(basename "$path") "*) ;;
            *) rm -f "$path" && REVOKED=$((REVOKED + 1)) ;;
        esac
    done
    [ "$REVOKED" = 0 ] || log "отозвано ссылок (клиент удалён, отключён или сменил токен): $REVOKED"
fi
exit "$FAILED"
