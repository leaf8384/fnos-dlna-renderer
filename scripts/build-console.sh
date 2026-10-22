#!/bin/bash
# 编译设置界面（Go，静态链接）
#
# 产物：build/dlna-console
#
# CGO_ENABLED=0 => 纯静态，不依赖目标机 glibc，
# 所以在任何机器上交叉编译出来的都能直接跑在 fnOS 上。
# 不需要 Docker：有 Go 就行（Debian 12 自带的 1.19 足够）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/console"
OUT="${ROOT}/build/dlna-console"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

say "检查 Go"
command -v go >/dev/null 2>&1 || die "没装 Go。Debian/Ubuntu: apt install golang；或从 https://go.dev/dl/ 解压一个到 PATH。"
go version

say "编译 dlna-console"
mkdir -p "${ROOT}/build"
cd "$SRC"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
	go build -trimpath -ldflags "-s -w" -o "$OUT" .

say "完成"
ls -la "$OUT"
printf '    大小: %s\n' "$(du -h "$OUT" | awk '{print $1}')"
