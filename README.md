# DLNA 音频桥

把 fnOS 变成一台 **DLNA/UPnP 音频渲染器**：手机上的网易云、QQ 音乐、酷狗、Apple Music（第三方 App）的投屏列表里会直接出现这台 NAS，点一下，声音就从 NAS 的 3.5mm 或 HDMI 口推给音箱。

底子是 [gmediarender 0.3.1](https://github.com/hzeller/gmrender-resurrect)，外加一组针对真实控制点行为的补丁。

---

## 为什么要打补丁

gmediarender 上游本身能跑，但用网易云投屏时有一批稳定的故障。这组补丁就是为它们写的：

| 现象 | 根因 | 补法 |
|---|---|---|
| 切歌后音箱还在放上一首 | 网易云切歌只发 `SetAVTransportURI`，不发 `Play` | 记录播放意图，新 URI 直接按播放态起流 |
| 直接切没声音，先暂停再切就正常 | 控制点发 `Seek(0)` 紧跟着 `SetAVTransportURI`，flush seek 让管线瞬间进 `PAUSED`，采样到这一瞬就把意图降级成暂停 | **意图只升不降**，只有显式 `Pause`/`Stop` 能清掉 |
| 连切几首卡死，进度条冻住 | 重建管线时 `set_state(READY)` 掐断旧 HTTP 连接，`soupsrc` 抛一条假 ERROR，约 1ms 后砸在新流的 preroll 上 | 400ms 合并窗口 + 拆流期错误降级 + 进程内进度看门狗 |
| 一首放完再点播放报 500 / 704 | 上游 `about-to-finish` 在没有下一首时把当前 URI 置空 | 无下一首就保持当前 URI |
| 上一首的 EOS 把 transport 打成 `STOPPED` | 陈旧 EOS 消息没被过滤 | 有新 URI 排队、或距起播不足 2.5s 的 EOS 一律忽略 |
| 每次启动音量都是 100%，第一次投流很吓人 | `upnp_control_init()` 只能从 sink 回读，全新启动恒为满量程 | 音量落盘 + 启动恢复 |

除此之外还有一条防御性处理：看门狗要连续三个采样窗都没进展才判断为卡死，避免一次 `query_position` 抖动就误杀正常的流。

补丁位于 [`patches/`](patches/)，是标准 unified diff，可以直接 `patch -p1`。

## 前置条件

- 一台 fnOS（Debian 12 底），且 **有能被 ALSA 驱动的声卡**
- 音箱接在 3.5mm 耳机口或 HDMI 上
- 声卡如果是直通给虚拟机的，需要在**那台虚拟机里**装，不是 Unraid 宿主

## 安装

### 方式一：FPK（推荐）

```bash
# 在 Linux 上构建，产物在 dist/
bash scripts/build.sh
bash scripts/package-fpk.sh
```

把 `dist/dlna-renderer_v1.0.1_x86.fpk`（或 Release 里的最新版）传到 NAS，然后 **应用中心 → 设置 → 手动安装**。

装完会：自动补齐 GStreamer 组件 → 生成配置 → 拉起进程。手机投屏列表里出现 `Marshall`。

### 方式二：直接装进系统（systemd）

适合不想动应用中心、或者想马上验证效果的场景：

```bash
bash scripts/build.sh
sudo bash scripts/install-standalone.sh
```

它会装二进制到 `/usr/local/bin`、写 `/etc/default/dlna-renderer`、装 systemd 服务并启动。

> 两种方式互斥。已经用 fpk 装了就别再跑脚本版，否则两个进程会抢同一块声卡。

## 配置

| 部署方式 | 配置文件 | 改完生效方式 |
|---|---|---|
| FPK | `<应用数据目录>/renderer.conf` | 应用中心停用再启用 |
| systemd | `/etc/default/dlna-renderer` | `systemctl restart gmediarender` |

三个参数：

```bash
FRIENDLY_NAME=Marshall                        # 手机上显示的名字
IFACE=enp5s0                                  # 绑定网卡，留空自动探测
AUDIODEV=plughw:CARD=PCH,DEV=0                # ALSA 设备名，用名字不用序号
```

`AUDIODEV` 一定要写 **CARD 名**（`PCH`）而不是编号（`hw:0`）—— 重启后 card 编号会变，名字不会。

`FRIENDLY_NAME` 可以写中文（如 `客厅音箱`）。飞牛拉起应用的环境里**没有任何 `LANG`**（只有它自己的 `TRIM_SYS_LANGUAGE=zh-CN`，glibc 不认），
所以 `cmd/main` 里显式 `export LANG=C.UTF-8 LC_ALL=C.UTF-8`。少了这一行，中文名会直接死在
`Failed to initialize: Invalid byte sequence in conversion input`。自己改启动脚本时别删掉。

查可用设备：

```bash
aplay -L | grep plughw
```

## 从源码重建

仓库自带上游源码快照（`vendor/gmrender.tar.gz`，108 KB），构建不依赖外网，也不会因为上游删库而失效。

```bash
# Debian / Ubuntu
sudo apt install -y build-essential autoconf automake libtool pkg-config \
                    libupnp-dev libgstreamer1.0-dev

bash scripts/build.sh              # 解压 → 打补丁 → autogen → configure → make
bash scripts/package-fpk.sh        # 组装成 fpk
```

`build.sh` 打完补丁后会打印 `patch` 的输出，两个补丁都应该是 `succeeded`。

> 构建机的 glibc 不能比目标机新，否则运行时报 `GLIBC_2.xx not found`。在 Debian 12 上构建的包可以跑在 Debian 12/13；在 Debian 13 上构建的**不能**跑在 Debian 12。

## 作为 FnDepot 源分发

仓库根目录的 `fnpack.json` 就是一份 FnDepot V2 源索引。想让另一台机器也能装、或者想要更新提醒时走这条路。

**先说清楚一点：FnDepot 客户端本身也是一个 fpk**，它不在飞牛官方应用中心里，得手动装。

1. 下载客户端（约 10 MB）：

   ```
   https://github.com/EWEDLCM/FnDepot/releases/download/v0.4.6/fndepot.v0.4.6.fpk
   ```

   飞牛「应用中心 → 设置 → 手动安装」选它。

2. 打开 FnDepot → **外部源管理 → 添加源**，填的是**仓库地址**，不是个人主页：

   ```
   https://github.com/leaf8384/fnos-dlna-renderer
   ```

   填成 `https://github.com/leaf8384` 会同步失败——主页根目录没有 `fnpack.json`。

3. 同步状态变成「成功」后，搜索「DLNA 音频桥」。装完之后和手动装 fpk 完全一样。

几个硬性要求：

- 仓库必须是 **Public**。Private 仓库客户端取不到 `fnpack.json`，除非在客户端里配了 Access Token。
- 客户端只认仓库根目录、文件名完全匹配的 `fnpack.json`，改目录或改名都不行。
- 源里写的相对路径 `./dist/*.fpk` 相对仓库根解析，所以 fpk 必须一起提交，不能只放 Release。

只给自己用的话，**完全不用装 FnDepot**：直接手动装 `dist/` 里的 fpk 更省事，少一层依赖。

发新版本：

```bash
# 先改 fpk/manifest 里的 version
bash scripts/build.sh
bash scripts/package-fpk.sh
python3 scripts/update-fnpack.py
git add -A && git commit -m "release v1.0.1" && git push
```

`update-fnpack.py` 会把新的 sha256 和文件大小写回 `fnpack.json`，漏了这一步客户端会因为哈希对不上而拒绝安装。

### 两个 fpk 的关系

仓库里会出现两个功能完全相同、但哈希不同的包，别混用：

| 位置 | 来源 | 谁在用 |
|---|---|---|
| `dist/*.fpk` | 提交进仓库的版本 | **`fnpack.json` 的相对路径指向它**，FnDepot 装的是这个 |
| Release 附件 | CI 在 `debian:12` 容器里重新编译的 | 想直接下载的人 |

两边是分别编译的，二进制里嵌的构建路径不同，所以 sha256 必然不一样。`fnpack.json` 记的是 `dist/` 那个。**换包必须同步更新 `fnpack.json`**，否则客户端校验直接拒装。

CI 用容器而非宿主机编译，是因为 GitHub runner 镜像预装了一堆包，`libgstreamer1.0-dev` 会和 `libunwind-dev` 的版本约束打架（`held broken packages`）。用 `debian:12` 容器既绕开这个坑，又让 glibc 和 fnOS 精确对齐——产物最高只要求 `GLIBC_2.34`，fnOS 是 2.36，留有余量。

## 排障

| 症状 | 先查什么 |
|---|---|
| 手机上搜不到设备 | `IFACE` 是否指向正确的网卡；`ss -ulpn \| grep 1900` 有没有监听；防火墙是否挡了 SSDP 组播 |
| 能找到设备但没声音 | `aplay -l` 确认声卡还在；`AUDIODEV` 的 CARD 名对不对；`alsamixer` 有没有被静音 |
| 切歌后没声 | 看日志里 `Switch intent: state=X want_play=Y`，正常切歌时 `want_play` 应为 1 |
| 声音断续 / 卡顿 | 无线音箱或弱网；确认音源码率与网络带宽匹配 |
| 启动即退出 | `journalctl -u gmediarender -n 50`，多半是 ALSA 设备名写错 |

日志里几个值得留意的信号：

- `Switch intent: state=3 want_play=1` —— 正是 Seek 竞态被化解的瞬间，属正常
- `teardown error ignored` —— 拆流假错误被正确降级，属正常
- `EOS of the outgoing stream ignored` —— 陈旧 EOS 被拦下，属正常
- `Restarting playbin` —— 兜底重建。偶发正常，频繁说明流不稳

## 目录结构

```
patches/          两个 unified diff，按序应用到 src/
vendor/           上游源码快照，离线可构建
fpk/              FPK 工程：manifest / cmd / config / wizard / 图标
scripts/
  build.sh            源码 -> 二进制
  package-fpk.sh      二进制 -> .fpk
  install-standalone.sh   直接装进系统（systemd）
  make-icon.py        生成 ICON.PNG / ICON_256.PNG
systemd/          systemd unit（script 版部署用）
tests/            行为验证脚本，需要一台在跑的渲染器
docs/             部署与恢复备忘
```

`tests/` 里是当初定位问题时写的复现脚本，直接用 `python3 t7.py --uri ...` 跑，用来验证竞态修复没被回退。它们不是单元测试，是**对着真实渲染器打**的集成脚本。

## 许可

补丁基于 GPL-2.0+ 的 gmediarender，本仓库整体同样以 GPL-2.0 发布。上游版权归原作者，改动部分见 `git log`。
