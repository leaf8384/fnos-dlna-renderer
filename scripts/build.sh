#!/bin/bash
# 从源码构建打过补丁的 gmediarender
#
# 产物：build/<srcdir>/src/gmediarender（x86-64 ELF）
# 已在 Debian 12 (bookworm) 与 Ubuntu 22.04 验证。
# 构建机 glibc 不能比目标机新，否则运行时会报 GLIBC_x.xx not found。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_TARBALL="${ROOT}/vendor/gmrender.tar.gz"
BUILD_DIR="${ROOT}/build"
PATCH_DIR="${ROOT}/patches"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

say "检查构建依赖"
[ -f "$VENDOR_TARBALL" ] || die "缺少 $VENDOR_TARBALL"

MISSING=""
for c in gcc make autoconf automake libtoolize pkg-config patch tar; do
	command -v "$c" >/dev/null 2>&1 || MISSING="$MISSING $c"
done
for m in gstreamer-1.0 gstreamer-app-1.0 libupnp glib-2.0; do
	pkg-config --exists "$m" 2>/dev/null || MISSING="$MISSING dev:$m"
done

if [ -n "$MISSING" ]; then
	die "缺少构建依赖:$MISSING

Debian / Ubuntu 上执行：
  apt install -y build-essential autoconf automake libtool pkg-config \\
                 libupnp-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev"
fi

say "解压上游源码"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
tar xzf "$VENDOR_TARBALL" -C "$BUILD_DIR"
SRC_DIR=$(find "$BUILD_DIR" -maxdepth 1 -mindepth 1 -type d | head -1)
[ -n "$SRC_DIR" ] || die "源码解压后没有找到目录"
echo "    $SRC_DIR"

say "应用补丁"
for p in "$PATCH_DIR"/*.patch; do
	echo "    $(basename "$p")"
	patch -p1 -d "$SRC_DIR" -i "$p"
done

say "生成 configure"
if [ ! -x "$SRC_DIR/configure" ]; then
	(cd "$SRC_DIR" && NOCONFIGURE=1 ./autogen.sh >/dev/null)
fi

say "配置"
(cd "$SRC_DIR" && ./configure --prefix=/usr >/dev/null)

say "编译（-j${JOBS}）"
(cd "$SRC_DIR" && make -j"$JOBS")

BIN="$SRC_DIR/src/gmediarender"
[ -x "$BIN" ] || die "编译产物不存在：$BIN"

say "完成"
ls -la "$BIN"
file "$BIN" 2>/dev/null || true
echo
echo "下一步：bash scripts/package-fpk.sh"
