#!/bin/sh
#
# shadow-pipeline.sh — теневой прогон нового конвейера (nodes_fetch → nodes_config) на роутере.
# Пишет ТОЛЬКО в $DATA (не в /etc/sing-box-subscribe), sing-box не трогает. Нужен, чтобы
# погонять новые модули на живых подписках до переключения cron на них (Ф3).
#
#   shadow-pipeline.sh [main|wh]      (по умолчанию main)
#
# Конфиги v2 берутся из $DATA/config-<set>/ (создаются migrate из боевых v1 при первом запуске).
# Итог прогона — строка в $DATA/shadow.log: число нод, changed, расхождение с боевым nodes.json.

set -u

PROJECT_DIR="/root/sing-box-nodes_tester"
DATA="/root/nodes-data"
SET="${1:-main}"

case "$SET" in
    main) V1="$PROJECT_DIR/config";           PROD="/etc/sing-box-subscribe/nodes.json" ;;
    wh)   V1="$PROJECT_DIR/config_whitelist"; PROD="/etc/sing-box-subscribe/whnodes.json" ;;
    *) echo "набор: main | wh" >&2; exit 2 ;;
esac

CFG="$DATA/config-$SET"
RAW="$DATA/raw/$SET.json"
OUT="$DATA/nodes-$SET.json"
LOG="$DATA/shadow.log"
mkdir -p "$DATA/raw"
cd "$PROJECT_DIR" || exit 1

if [ ! -f "$CFG/providers.json" ]; then
    python3 -m nodes_config migrate --from "$V1" --to "$CFG" || exit 1
fi

FETCH="$(python3 -m nodes_fetch -p "$CFG/providers.json" -o "$RAW" --json 2>>"$DATA/shadow-$SET.err")"
FCODE=$?
UN=""
[ -f "$CFG/user_nodes.json" ] && UN="--user-nodes $CFG/user_nodes.json"
CONF="$(python3 -m nodes_config --raw "$RAW" --groups "$CFG/groups_params.json" $UN -o "$OUT" --json 2>>"$DATA/shadow-$SET.err")"
CCODE=$?

python3 - "$SET" "$FCODE" "$CCODE" "$OUT" "$PROD" "$FETCH" "$CONF" >> "$LOG" <<'EOF'
import json, sys, time
st, fcode, ccode, out, prod, fetch, conf = sys.argv[1:8]
def tags(p):
    try:
        d = json.load(open(p, encoding="utf-8"))
        return {o["tag"] for o in d.get("outbounds", []) + d.get("endpoints", [])}
    except (OSError, ValueError):
        return set()
f = json.loads(fetch) if fetch.strip() else {}
c = json.loads(conf) if conf.strip() else {}
failed = [s["provider"] + ("(stale)" if s.get("stale") else "") for s in f.get("sources", []) if not s.get("ok")]
new, old = tags(out), tags(prod)
print(f"{time.strftime('%F %T')} {st}: fetch={fcode} raw={f.get('nodes')} fail={failed or '-'} "
      f"config={ccode} changed={c.get('changed')} leaf={c.get('report', {}).get('leaf_nodes')} "
      f"vs_prod: common={len(new & old)} only_new={len(new - old)} only_prod={len(old - new)}")
EOF
tail -1 "$LOG"
