#!/bin/bash
# 不走应用中心，直接把渲染器装进系统（systemd 托管）
#
# 用法：
#   sudo bash scripts/install-standalone.sh [二进制路径]
#
# 不给路径就用 build/ 下的编译产物。重复执行等于升级，配置和音量都会保留。
#
# 这条路径和 FPK 互斥：装了 fpk 就不要再跑这个，否则两个进程会抢声卡。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_SRC="${1:-}"
BIN_DST=/usr/local/bin/gmediarender
UNIT_DST=/etc/systemd/system/gmediarender.service
ENV_FILE=/etc/default/dlna-renderer
STATE_DIR=/var/lib/gmediarender
DEPS="gstreamer1.0-alsa gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

[ "$(id -u)" -eq 0 ] || die "需要用 root 执行：sudo bash $0"

if [ -z "$BIN_SRC" ]; then
	BIN_SRC=$(find "${ROOT}/build" -maxdepth 3 -name gmediarender -type f -perm -u+x 2>/dev/null | head -1 || true)
fi
[ -n "$BIN_SRC" ] && [ -f "$BIN_SRC" ] || die "找不到二进制。先跑 bash scripts/build.sh，或把路径作为参数传进来。"

say "安装运行依赖"
NEED=""
for pkg in $DEPS; do
	dpkg -s "$pkg" >/dev/null 2>&1 || NEED="$NEED $pkg"
done
if [ -n "$NEED" ]; then
	echo "    安装:$NEED"
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -qq || true
	apt-get install -y -qq $NEED
else
	echo "    依赖已满足"
fi

say "安装二进制"
install -m 0755 "$BIN_SRC" "$BIN_DST"
ls -la "$BIN_DST"

say "写入默认配置"
if [ -f "$ENV_FILE" ]; then
	echo "    $ENV_FILE 已存在，保留不动"
else
	IFACE=$(ip -4 route show default 2>/dev/null |
		awk '{ for (n = 1; n <= NF; n++) if ($n == "dev") { print $(n + 1); exit } }')
	CARD=$(aplay -l 2>/dev/null | sed -n 's/^card [0-9]*: \([^ ]*\) \[.*/\1/p' | head -1)
	cat >"$ENV_FILE" <<EOF
# DLNA 渲染器参数。改完执行 systemctl restart gmediarender

# 手机投屏列表里显示的名字
FRIENDLY_NAME=Marshall

# 绑定网卡。多网卡或改过网络后如果手机搜不到设备，先确认这里。
IFACE=${IFACE:-eth0}

# ALSA 输出设备。查可用值：aplay -L | grep plughw
AUDIODEV=plughw:CARD=${CARD:-PCH},DEV=0

# 音量存档位置
GMR_VOLUME_STATE=${STATE_DIR}/volume.state
EOF
	echo "    已生成 $ENV_FILE（网卡=${IFACE:-未知} 声卡=${CARD:-未知}）"
fi

say "安装 systemd 服务"
install -m 0644 "${ROOT}/systemd/gmediarender.service" "$UNIT_DST"
mkdir -p "$STATE_DIR"
systemctl daemon-reload

say "启动"
systemctl enable gmediarender >/dev/null
systemctl restart gmediarender
sleep 2

if systemctl is-active --quiet gmediarender; then
	echo
	echo "已启动。近几行日志："
	journalctl -u gmediarender -n 8 --no-pager || true
else
	echo
	echo "启动失败，日志如下："
	journalctl -u gmediarender -n 30 --no-pager || true
	exit 1
fi

echo
echo "接下来：手机音乐 App 的投屏列表里应该能看到「$(sed -n 's/^FRIENDLY_NAME=//p' "$ENV_FILE" | head -1)」。"
