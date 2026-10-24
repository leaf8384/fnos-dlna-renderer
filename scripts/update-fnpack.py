#!/usr/bin/env python3
"""把打好的 fpk 回填进 fnpack.json。

FnDepot 的 V2 源要求每个安装包分支填真实的 download_url、sha256、size。
手工算容易漏，这里在读 manifest 拿到版本号后一次性写好。

用法：
    python scripts/update-fnpack.py                      # 自动找 dist/ 下最新的 fpk
    python scripts/update-fnpack.py dist/xxx.fpk
    python scripts/update-fnpack.py --url https://example.com/xxx.fpk

不给 --url 时写相对路径 ./dist/<文件名>，适合把 fpk 一起提交进仓库的用法；
如果用 GitHub Release 分发，就传具体链接。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FPK_DIR = ROOT / "fpk"
DIST_DIR = ROOT / "dist"
SOURCE_JSON = ROOT / "fnpack.json"


def manifest_value(key: str) -> str:
    """从 fpk/manifest 里取一个 key 的值。"""
    for line in (FPK_DIR / "manifest").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    return ""


def pick_fpk(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            sys.exit(f"找不到文件：{path}")
        return path

    expected = DIST_DIR / f"{manifest_value('appname')}_v{manifest_value('version')}_{manifest_value('platform') or 'x86'}.fpk"
    if not expected.is_file():
        sys.exit(f"缺少当前版本安装包：{expected.name}，请先打包；不会使用旧版本代替。")
    return expected


def verify_package(path: Path, appname: str, version: str, platform: str) -> None:
    """Validate the archive itself, not its filename or modification time."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            entries = [m for m in archive.getmembers() if m.name.removeprefix("./") == "manifest"]
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 128 * 1024:
                raise ValueError("包内 manifest 缺失、重复或无效")
            stream = archive.extractfile(entries[0])
            assert stream is not None
            values = {}
            for line in stream.read().decode("utf-8").splitlines():
                if line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
            for key, expected in (("appname", appname), ("version", version), ("platform", platform)):
                if values.get(key) != expected:
                    raise ValueError(f"{key} 不匹配：包内 {values.get(key)!r}，项目 {expected!r}")
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        sys.exit(f"拒绝更新索引：{exc}")


def digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def main() -> int:
    parser = argparse.ArgumentParser(description="把 fpk 的哈希与大小写进 fnpack.json")
    parser.add_argument("fpk", nargs="?", help="fpk 路径，默认取 dist/ 下最新的")
    parser.add_argument("--url", help="下载地址，默认 ./dist/<文件名>")
    args = parser.parse_args()

    fpk = pick_fpk(args.fpk)
    appname = manifest_value("appname")
    version = manifest_value("version")
    platform = manifest_value("platform") or "x86"

    if not appname or not version:
        sys.exit("manifest 里读不到 appname 或 version")

    verify_package(fpk, appname, version, platform)
    sha256, size = digest(fpk)
    url = args.url or f"./dist/{fpk.name}"

    data = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    apps = data.setdefault("apps", {})
    app = apps.setdefault(appname, {})
    releases = app.setdefault("releases", {})
    release = releases.setdefault(version, {})
    packages = release.setdefault("packages", {})

    packages[platform] = {
        "download_url": url,
        "sha256": sha256,
        "size": size,
    }

    SOURCE_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"已更新 fnpack.json")
    print(f"  appname : {appname}")
    print(f"  version : {version}")
    print(f"  platform: {platform}")
    print(f"  file    : {fpk.name}  ({size} bytes)")
    print(f"  sha256  : {sha256}")
    print(f"  url     : {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
