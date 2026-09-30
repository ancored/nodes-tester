#!/bin/sh
#
# build.sh — собрать пакет nodes-tester в OpenWrt SDK (docker) и положить его в dist/.
#
#   SIGN_KEY=/path/private-key.pem openwrt/build.sh [SDK_TAG]    по умолчанию x86-64-25.12.2
#
# Пакет архитектурно-независимый (PKGARCH:=all), так что годится SDK любой платформы —
# важна только версия OpenWrt (25.x собирает .apk, 24.10 и старше — .ipk).
#
# SIGN_KEY — закрытый ключ ECDSA P-256 проекта; открытый — openwrt/nodes-tester.pem. С ключом
# .apk подписывается (`apk adbsign`), и на роутере с установленным открытым ключом ставится без
# --allow-untrusted. Без SIGN_KEY пакет остаётся неподписанным.

set -eu

SDK_TAG="${1:-x86-64-25.12.2}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/dist"
KEYS=""
if [ -n "${SIGN_KEY:-}" ]; then
    [ -f "$SIGN_KEY" ] || { echo "SIGN_KEY: нет файла $SIGN_KEY" >&2; exit 1; }
    KEYS="$(cd "$(dirname "$SIGN_KEY")" && pwd)"
    KEYNAME="$(basename "$SIGN_KEY")"
fi
case "$(uname -s)" in   # Bash на Windows: docker нужен путь вида C:/...
    MINGW*|MSYS*) REPO="$(cd "$REPO" && pwd -W)"; [ -n "$KEYS" ] && KEYS="$(cd "$KEYS" && pwd -W)"
                  export MSYS_NO_PATHCONV=1 ;;
esac

# /builder — в именованном томе: собранные host-инструменты и зависимости переживают запуск
docker run --rm -v "nodes-tester-sdk-$SDK_TAG:/builder" \
    -v "$REPO:/src:ro" -v "$REPO/dist:/dist" ${KEYS:+-v "$KEYS:/keys:ro"} \
    -e KEYNAME="${KEYNAME:-}" "openwrt/sdk:$SDK_TAG" sh -ec '
    [ -x ./setup.sh ] && [ ! -d staging_dir ] && ./setup.sh >/dev/null
    # только свой фид: зависимости ставит apk на роутере (EXTRA_DEPENDS), собирать их не нужно
    echo "src-link nodestester /src/openwrt" > feeds.conf
    ./scripts/feeds update nodestester >/dev/null
    ./scripts/feeds install -p nodestester nodes-tester >/dev/null
    make defconfig >/dev/null
    # код берётся из /src, а OpenWrt отслеживает изменения только в openwrt/nodes-tester:
    # без clean подготовленный каталог в томе остаётся от прошлой сборки той же версии
    make package/nodes-tester/clean >/dev/null
    make package/nodes-tester/compile -j"$(nproc)" V=s
    # прежние версии в dist/ не удаляем: они нужны для отката
    for f in $(find bin/packages -name "nodes-tester*" \( -name "*.apk" -o -name "*.ipk" \)); do
        cp -v "$f" /dist/
        case "$f" in
            *.apk) if [ -n "$KEYNAME" ]; then
                       ./staging_dir/host/bin/apk adbsign --allow-untrusted \
                           --sign-key "/keys/$KEYNAME" "/dist/$(basename "$f")"
                       echo "подписан: $(basename "$f")"
                   else
                       echo "SIGN_KEY не задан — $(basename "$f") не подписан"
                   fi ;;
        esac
    done
'
