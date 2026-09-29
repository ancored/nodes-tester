#!/bin/sh
# Update sing-box rules from /etc/nodes-tester/singbox/rules.json.
# The lock is shared with the complete pipeline and manual CLI runs.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$HERE/../.." && pwd)}"
PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
DATA="${DATA:-/root/nodes-data}"
. "$HERE/pipeline-lock.sh"
trap 'pipeline_lock_release' 0
pipeline_lock_acquire || exit $?
python3 -m nodes_admin.rules "$@"
exit $?
