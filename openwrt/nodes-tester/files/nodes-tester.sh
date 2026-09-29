#!/bin/sh
#
# nodes-tester — запуск компонентов установленного пакета с путями из /etc/config/nodes-tester.
#
#   nodes-tester fetch    [args]   python3 -m nodes_fetch
#   nodes-tester config   [args]   python3 -m nodes_config
#   nodes-tester tester   [args]   python3 -m nodes_tester -c <config_dir>/config.json
#   nodes-tester vacuum            VACUUM stats.db (для cron раз в месяц)
#   nodes-tester pipeline router|clients [--dry-run]
#   nodes-tester backup   [--stable] [--pkg FILE]   снимок перед обновлением
#   nodes-tester rollback [-y] [--keep-db] [--check] [SNAPSHOT]
#
# Команды выполняются из data_dir, поэтому относительные пути (stats.db) те же, что у сервиса.

. /lib/functions.sh

APP_DIR=/usr/lib/nodes-tester

config_load nodes-tester
config_get CONFIG_DIR main config_dir /etc/nodes-tester
config_get DATA_DIR main data_dir /opt/nodes-tester

export PYTHONPATH="$APP_DIR" PYTHONIOENCODING=utf-8 CONFIG_DIR
mkdir -p "$DATA_DIR"
cd "$DATA_DIR" || exit 1

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
	fetch)  exec python3 -m nodes_fetch "$@" ;;
	config) exec python3 -m nodes_config "$@" ;;
	tester) exec python3 -m nodes_tester -c "$CONFIG_DIR/config.json" "$@" ;;
	vacuum) exec python3 -m nodes_tester -c "$CONFIG_DIR/config.json" --vacuum ;;
	pipeline)
		PROJECT_DIR="$APP_DIR" DATA="$DATA_DIR" CFG_ROOT="$CONFIG_DIR" \
		CLIENTS_FILE="${CLIENTS_FILE:-$CONFIG_DIR/clients.list}" \
			exec "$APP_DIR/scripts/router/pipeline.sh" "$@" ;;
	backup)   exec "$APP_DIR/scripts/router/backup-nodes-tester.sh" "$@" ;;
	rollback) exec "$APP_DIR/scripts/router/rollback-nodes-tester.sh" "$@" ;;
	*)
		sed -n '3,13s/^# \{0,1\}//p' "$0" >&2
		exit 2 ;;
esac
