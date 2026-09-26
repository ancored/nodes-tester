#!/bin/sh
#
# build.sh — собрать пакет nodes-tester в OpenWrt SDK (docker) и положить его в dist/.
#
#   openwrt/build.sh [SDK_TAG]      по умолчанию x86-64-25.12.2
#
# Пакет архитектурно-независимый (PKGARCH:=all), так что годится SDK любой платформы —
# важна только версия OpenWrt (25.x собирает .apk, 24.10 и старше — .ipk).

set -eu

SDK_TAG="${1:-x86-64-25.12.2}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/dist"
case "$(uname -s)" in   # Git Bash на Windows: docker нужен путь вида C:/...
    MINGW*|MSYS*) REPO="$(cd "$REPO" && pwd -W)"; export MSYS_NO_PATHCONV=1 ;;
esac

# /builder — в именованном томе: собранные host-инструменты и зависимости переживают запуск
docker run --rm -v "nodes-tester-sdk-$SDK_TAG:/builder" \
    -v "$REPO:/src:ro" -v "$REPO/dist:/dist" "openwrt/sdk:$SDK_TAG" sh -ec '
    [ -x ./setup.sh ] && [ ! -d staging_dir ] && ./setup.sh >/dev/null
    # только свой фид: зависимости ставит apk на роутере (EXTRA_DEPENDS), собирать их не нужно
    echo "src-link nodestester /src/openwrt" > feeds.conf
    ./scripts/feeds update nodestester >/dev/null
    ./scripts/feeds install -p nodestester nodes-tester >/dev/null
    make defconfig >/dev/null
    make package/nodes-tester/compile -j"$(nproc)" V=s
    rm -f /dist/nodes-tester*
    find bin/packages -name "nodes-tester*" \( -name "*.apk" -o -name "*.ipk" \) -exec cp -v {} /dist/ \;
'
