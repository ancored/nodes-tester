#!/bin/sh
#
# publish-repo.sh — выложить подписанные .apk в репозиторий пакетов на GitHub Pages
# (ветка gh-pages): https://ancored.github.io/nodes-tester/packages.adb
#
#   SIGN_KEY=/path/private-key.pem openwrt/publish-repo.sh dist/nodes-tester-X.Y.Z-rN.apk [...]
#
# Пакеты накапливаются на ветке (можно откатиться на прежнюю версию); индекс packages.adb
# пересобирается по всем .apk ветки и подписывается тем же ключом, что и пакеты.

set -eu

[ $# -gt 0 ] || { echo "укажите .apk для публикации" >&2; exit 1; }
[ -f "${SIGN_KEY:-}" ] || { echo "SIGN_KEY: нужен закрытый ключ" >&2; exit 1; }
SDK_TAG="${SDK_TAG:-x86-64-25.12.2}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SITE="$REPO/dist/gh-pages"; WT="dist/gh-pages"   # git на Windows не понимает /c/... в worktree

cd "$REPO"
git fetch -q origin gh-pages 2>/dev/null || true
rm -rf "$SITE"; git worktree prune
if git rev-parse -q --verify origin/gh-pages >/dev/null; then
    git worktree add -q -B gh-pages "$WT" origin/gh-pages
else
    git worktree add -q --detach "$WT"
    (cd "$SITE" && git checkout -q --orphan gh-pages && git rm -rqf .)
fi

for f in "$@"; do cp -v "$f" "$SITE/"; done
cp openwrt/nodes-tester.pem "$SITE/nodes-tester.pem"
touch "$SITE/.nojekyll"

KEYS="$(cd "$(dirname "$SIGN_KEY")" && pwd)"; KEYNAME="$(basename "$SIGN_KEY")"; OUT="$SITE"
case "$(uname -s)" in
    MINGW*|MSYS*) KEYS="$(cd "$KEYS" && pwd -W)"; OUT="$(cd "$SITE" && pwd -W)"; export MSYS_NO_PATHCONV=1 ;;
esac
docker run --rm -v "nodes-tester-sdk-$SDK_TAG:/builder" -v "$KEYS:/keys:ro" -v "$OUT:/repo" \
    "openwrt/sdk:$SDK_TAG" sh -ec "
    cd /repo
    /builder/staging_dir/host/bin/apk mkndx --allow-untrusted --sign-key /keys/$KEYNAME \
        --output packages.adb *.apk
    /builder/staging_dir/host/bin/apk --keys-dir /keys verify packages.adb *.apk"

{
    echo '<!doctype html><meta charset="utf-8"><title>nodes-tester apk</title>'
    echo '<h1>nodes-tester — репозиторий пакетов OpenWrt 25.x</h1>'
    echo '<p>Подключение: <a href="https://github.com/ancored/nodes-tester/blob/master/openwrt/README.md">openwrt/README.md</a></p><ul>'
    for f in "$SITE"/*.apk "$SITE/packages.adb" "$SITE/nodes-tester.pem"; do
        n="$(basename "$f")"; echo "<li><a href=\"$n\">$n</a></li>"
    done
    echo '</ul>'
} > "$SITE/index.html"

cd "$SITE"
git add -A
git commit -q -m "Publish $(for f in "$@"; do basename "$f"; done | tr '\n' ' ')"
git push -q origin gh-pages
cd "$REPO"; git worktree remove --force "$WT"
echo "опубликовано: https://ancored.github.io/nodes-tester/packages.adb"
