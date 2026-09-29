#!/bin/sh
# Shared by pipeline.sh and update-rules.sh.
pipeline_lock_release() {
    [ "${PIPELINE_LOCK_HELD:-}" = 1 ] || return 0
    if [ -n "${pipeline_lock_dir:-}" ] &&
       [ "$(cat "$pipeline_lock_dir/pid" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$pipeline_lock_dir/pid"
        rmdir "$pipeline_lock_dir" 2>/dev/null || true
    fi
}

pipeline_lock_acquire() {
    [ "${PIPELINE_LOCK_HELD:-}" = 1 ] && return 0
    mkdir -p "$DATA" || return 1
    lock_file="$DATA/pipeline.lock"
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$lock_file" || return 1
        if ! flock -n 9; then
            echo '[pipeline] другой конвейер уже выполняется' >&2
            return 75
        fi
    else
        lock_dir="$lock_file.d"
        if ! mkdir "$lock_dir" 2>/dev/null; then
            old_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
            case "$old_pid" in
                ''|*[!0-9]*) echo '[pipeline] другой конвейер уже выполняется' >&2; return 75 ;;
                *) if kill -0 "$old_pid" 2>/dev/null; then
                       echo '[pipeline] другой конвейер уже выполняется' >&2
                       return 75
                   fi ;;
            esac
            # Only one contender may reclaim a stale lock at a time.
            reclaim="$lock_dir.reclaim"
            mkdir "$reclaim" 2>/dev/null || return 75
            [ "$(cat "$lock_dir/pid" 2>/dev/null || true)" = "$old_pid" ] || {
                rmdir "$reclaim"; return 75;
            }
            rm -f "$lock_dir/pid"
            if ! rmdir "$lock_dir" 2>/dev/null || ! mkdir "$lock_dir" 2>/dev/null; then
                rmdir "$reclaim" 2>/dev/null || true
                return 75
            fi
            rmdir "$reclaim" 2>/dev/null || true
        fi
        echo "$$" > "$lock_dir/pid"
        pipeline_lock_dir="$lock_dir"
    fi
    PIPELINE_LOCK_HELD=1
    export PIPELINE_LOCK_HELD
}
