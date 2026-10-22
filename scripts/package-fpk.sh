#!/bin/bash
# 把构建产物打成一个可以安装的 .fpk
#
# 产物：dist/<appname>_v<version>_x86.fpk
#
# 必须在 Linux 上执行。FPK 里有大量可执行位和权限位，
# Windows / macOS 的 tar 打包会把这些信息弄丢，装上去脚本跑不起来。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FPK_SRC="${ROOT}/fpk"
BUILD_DIR="${ROOT}/build"
DIST_DIR="${ROOT}/dist"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

manifest_value() {
	sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "${FPK_SRC}/manifest" | head -1 | sed 's/[[:space:]]*$//'
}

APPNAME=$(manifest_value appname)
VERSION=$(manifest_value version)
[ -n "$APPNAME" ] || die "manifest 里读不到 appname"
[ -n "$VERSION" ] || die "manifest 里读不到 version"

say "检查输入"
BIN=$(find "$BUILD_DIR" -maxdepth 3 -name gmediarender -type f -perm -u+x 2>/dev/null | head -1)
[ -n "$BIN" ] || die "找不到构建产物，先执行：bash scripts/build.sh"
CONSOLE="${BUILD_DIR}/dlna-console"
[ -f "$CONSOLE" ] || die "找不到设置界面二进制，先执行：bash scripts/build-console.sh"
echo "    $APPNAME v$VERSION"
echo "    $BIN"
echo "    $CONSOLE"

say "准备暂存目录"
STAGE="${BUILD_DIR}/fpk-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "${FPK_SRC}/." "$STAGE/"

say "组装 app/"
mkdir -p "$STAGE/app"
install -m 0755 "$BIN" "$STAGE/app/gmediarender"
install -m 0755 "$CONSOLE" "$STAGE/app/dlna-console"
# 飞牛的桌面入口靠 app 目录下的 ui/ 描述：ui/config 声明「以 iframe 窗口打开」，
# ui/images 提供桌面图标。缺了这两样，应用中心里就没有图标、点了会甩到新标签页。
cp -a "${FPK_SRC}/app_ui" "$STAGE/app/ui"

# 飞牛安装时把 app.tgz 解开放到 TRIM_APPDEST，所以要带 ./ 前缀打成归档
tar czf "$STAGE/app.tgz" -C "$STAGE/app" --owner=0 --group=0 .
rm -rf "$STAGE/app"

say "修正权限"
chmod 0755 "$STAGE"/cmd/*
chmod 0644 "$STAGE"/config/* "$STAGE"/wizard/* "$STAGE"/manifest
chmod 0644 "$STAGE"/ICON.PNG "$STAGE"/ICON_256.PNG 2>/dev/null || true

say "写入 checksum"
CHECKSUM=$(md5sum "$STAGE/app.tgz" | awk '{print $1}')
sed -i '/^checksum[[:space:]]*=/d' "$STAGE/manifest"
printf 'checksum                   = %s\n' "$CHECKSUM" >>"$STAGE/manifest"
echo "    checksum = $CHECKSUM"

say "打包"
mkdir -p "$DIST_DIR"
OUT="${DIST_DIR}/${APPNAME}_v${VERSION}_x86.fpk"
rm -f "$OUT"
tar czf "$OUT" -C "$STAGE" --owner=0 --group=0 .

say "完成"
ls -la "$OUT"
echo "    sha256: $(sha256sum "$OUT" | awk '{print $1}')"
echo
echo "安装：飞牛「应用中心 → 设置 → 手动安装」，选择上面的 fpk。"
